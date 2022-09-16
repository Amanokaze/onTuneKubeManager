import os
import json
from kubernetes import client
from lib import utils

DEFAULT_INFO = {
    "KUBE_HOST": "http://localhost:6443",
    "KUBE_API_KEY": None,
    "KUBE_MGR_ST_INTERVAL": 10,
    "KUBE_MGR_LT_INTERVAL": 600,
    "KUBE_CLUSTER_NAME": "kubernetes",
}

class Kubedata:
    def __init__(self, log):
        # Initialization Variables
        self.ns_data = dict()
        self.node_data = dict()
        self.pod_data = dict()
        self.svc_data = dict()
        self.ds_data = dict()
        self.rs_data = dict()
        self.deploy_data = dict()
        self.sts_data = dict()

        self.node_metric_data = dict()
        self.pod_metric_data = dict()

        self.cluster_address = str()
        self.data_exist = False

        # Assign Initial Data
        self.log = log

        self.host = os.environ["KUBE_HOST"] if "KUBE_HOST" in os.environ else DEFAULT_INFO["KUBE_HOST"]
        self.api_key = os.environ["KUBE_API_KEY"] if "KUBE_API_KEY" in os.environ else DEFAULT_INFO["KUBE_API_KEY"]
        self.st_interval = int(os.environ["KUBE_MGR_ST_INTERVAL"])  if "KUBE_MGR_ST_INTERVAL" in os.environ else DEFAULT_INFO["KUBE_MGR_ST_INTERVAL"]
        self.lt_interval = int(os.environ["KUBE_MGR_LT_INTERVAL"])  if "KUBE_MGR_LT_INTERVAL" in os.environ else DEFAULT_INFO["KUBE_MGR_LT_INTERVAL"]
        self.cluster_name = os.environ["KUBE_CLUSTER_NAME"] if "KUBE_CLUSTER_NAME" in os.environ else DEFAULT_INFO["KUBE_CLUSTER_NAME"]

        # Kube Cluster 접속은 최초 1회만 이루어지며, Thread 별로 접속을 보장하지 않음
        self.cfg = client.Configuration()
        self.cfg.api_key['authorization'] = self.api_key
        self.cfg.api_key_prefix['authorization'] = 'Bearer'
        self.cfg.host = self.host
        self.cfg.verify_ssl = True
        self.cfg.ssl_ca_cert = 'ca.crt'

    def get_stats_api(self):
        self.api_version_info = client.CoreApi(client.ApiClient(self.cfg)).get_api_versions()
        self.cluster_address = self.api_version_info.server_address_by_client_cid_rs[0].server_address.split(":")[0]

        core_api = client.CoreV1Api(client.ApiClient(self.cfg))
        apps_api = client.AppsV1Api(client.ApiClient(self.cfg))

        self.get_kube_ns_data(core_api)
        self.get_kube_node_data(core_api)
        self.get_kube_pod_data(core_api)
        self.get_kube_svc_data(core_api)
        self.get_kube_ds_data(apps_api)
        self.get_kube_rs_data(apps_api)
        self.get_kube_deploy_data(apps_api)
        self.get_kube_sts_data(apps_api)

        if self.node_data and self.ns_data:
            self.data_exist = True

    def get_kube_node_data(self, api):
        try:
            nodes = api.list_node()
            node_data = dict()
            node_metric_data = dict()
            pod_metric_data = dict()
            
            for node in nodes.items:
                nodename = node.metadata.name

                node_data[nodename] = {
                    "uid": node.metadata.uid,
                    "name": nodename,
                    "nameext": nodename,
                    "enabled": 1,
                    "state": 1,
                    "connected": 1,
                    "starttime": utils.datetime_to_timestampz(node.metadata.creation_timestamp),
                    "kernelversion": node.status.node_info.kernel_version,
                    "osimage": node.status.node_info.os_image,
                    "osname": node.status.node_info.operating_system,
                    "containerruntimever": node.status.node_info.container_runtime_version,
                    "kubeletver": node.status.node_info.kubelet_version,
                    "kubeproxyver": node.status.node_info.kube_proxy_version,
                    "cpuarch": node.status.node_info.architecture,
                    "cpucount": node.status.capacity["cpu"],
                    "ephemeralstorage": utils.change_quantity_unit(node.status.capacity["ephemeral-storage"]),
                    "memorysize": utils.change_quantity_unit(node.status.capacity["memory"]),
                    "pods": node.status.capacity["pods"],
                    "ip": node.status.addresses[0].address
                }

                try:
                    node_stats = api.connect_get_node_proxy_with_path(nodename, "stats/summary")
                    node_stats_json = json.loads(node_stats.replace("'",'"'))
                    node_metric_data[nodename] = node_stats_json['node']
                    pod_metric_data[nodename] = node_stats_json['pods']
                except client.rest.ApiException as e:
                    node_metric_data[nodename] = dict()
                    pod_metric_data[nodename] = dict()
                    node_data[nodename]["state"] = 0
                    node_data[nodename]["connected"] = 0

            self.log.write("GET", "Kube Node Data Import is completed.")

            self.node_data = node_data
            self.node_metric_data = node_metric_data
            self.pod_metric_data = pod_metric_data

        except Exception as e:
            self.log.write("Error", str(e))

    def get_kube_pod_data(self, api):
        try:
            pods = api.list_pod_for_all_namespaces()
            pod_data = dict()

            for pod in pods.items:
                restarttime = 0
                restartcount = 0
                annotation = str()

                if pod.status.container_statuses:
                    restarttime = max(list(utils.datetime_to_timestampz(x.state.running.started_at) if x.state.running else 0 for x in pod.status.container_statuses))
                    restartcount = sum(list(utils.nvl_zero(x.restart_count) for x in pod.status.container_statuses))

                conditions = ",".join(list(
                    (f"{x.type}:{x.reason}" if x.reason else f"{x.type}") +
                    (f"-{utils.datetime_to_timestampz(x.last_transition_time)}") + 
                    (f":{utils.msg_str(x.message)}" if x.message else "")
                    for x in pod.status.conditions
                )) if pod.status.conditions else f"{pod.status.reason}:{utils.msg_str(pod.status.message)}"

                if pod.metadata.annotations:
                    if "kubernetes.io/config.hash" in pod.metadata.annotations:
                        annotation = pod.metadata.annotations["kubernetes.io/config.hash"]
                    elif "kubernetes.io/config.mirror" in pod.metadata.annotations:
                        annotation = pod.metadata.annotations["kubernetes.io/config.mirror"]

                pod_data[pod.metadata.uid] = {
                    "nodeid": 0,
                    "nsid": 0,
                    "uid": pod.metadata.uid,
                    "annotationuid": annotation,
                    "name": pod.metadata.name,
                    "starttime": utils.datetime_to_timestampz(pod.metadata.creation_timestamp),
                    "restartpolicy": pod.spec.restart_policy, 
                    "serviceaccount": pod.spec.service_account,
                    "status": pod.status.phase,
                    "hostip": utils.nvl_str(pod.status.host_ip),
                    "podip": utils.nvl_str(pod.status.pod_ip),
                    "restartcount": restartcount,
                    "restarttime": restarttime,
                    "condition": conditions,
                    "nodename": pod.spec.node_name,
                    "nsname": pod.metadata.namespace,
                    "refkind": pod.metadata.owner_references[0].kind if pod.metadata.owner_references else "",
                    "refid": 0,
                    "refuid": pod.metadata.owner_references[0].uid if pod.metadata.owner_references else "",
                    "containers": list() if pod.metadata.owner_references or pod.metadata.owner_references[0].kind != 'Node' else list({
                        "kind": "Pod",
                        "uid": pod.metadata.uid,
                        "name": x.name,
                        "image": x.image,
                        "ports": str(x.ports) if x.ports else "",
                        "env": str(x.env) if x.env else "",
                        "resources": str(x.resources) if x.resources else "",
                        "volumemounts": str(x.volume_mounts) if x.volume_mounts else ""
                    } for x in pod.spec.containers)
                }

            self.log.write("GET", "Kube Pod Data Import is completed.")
            self.pod_data = pod_data
        except Exception as e:
            self.log.write("Error", str(e))

    def get_kube_ns_data(self, api):
        try:
            nslist = api.list_namespace()
            self.ns_data = list({'name': x.metadata.name, 'status': x.status.phase} for x in nslist.items)
        except Exception as e:
            self.log.write("Error", str(e))

    def get_kube_svc_data(self, api):
        try:
            services = api.list_service_for_all_namespaces()
            svc_data = dict()
            for svc in services.items:
                svc_data[svc.metadata.uid] = {
                    "nsid": 0,
                    "name": svc.metadata.name,
                    "uid": svc.metadata.uid,
                    "starttime": utils.datetime_to_timestampz(svc.metadata.creation_timestamp),
                    "servicetype": svc.spec.type,
                    "clusterip": svc.spec.cluster_ip,
                    "ports": utils.dict_port_to_str(svc.spec.ports),
                    "selector": utils.dict_to_str(svc.spec.selector),
                    "nsname": svc.metadata.namespace
                }

            self.log.write("GET", "Kube Service Data Import is completed.")

            self.svc_data = svc_data                
        except Exception as e:
            self.log.write("Error", str(e))

    def get_kube_ds_data(self, api):
        try:
            daemonsets = api.list_daemon_set_for_all_namespaces()
            ds_data = dict()

            for ds in daemonsets.items:
                ds_data[ds.metadata.uid] = {
                    "nsid": 0,
                    "name": ds.metadata.name,
                    "uid": ds.metadata.uid,
                    "starttime": utils.datetime_to_timestampz(ds.metadata.creation_timestamp),
                    "serviceaccount": ds.spec.template.spec.service_account,
                    "current": utils.nvl_zero(ds.status.current_number_scheduled),
                    "desired": utils.nvl_zero(ds.status.desired_number_scheduled),
                    "ready": utils.nvl_zero(ds.status.number_ready),
                    "updated": utils.nvl_zero(ds.status.updated_number_scheduled),
                    "available": utils.nvl_zero(ds.status.number_available),
                    "selector": utils.dict_to_str(ds.spec.selector.match_labels),
                    "nsname": ds.metadata.namespace,
                    "containers": list({
                        "kind": "DaemonSet",
                        "uid": ds.metadata.uid,
                        "name": x.name,
                        "image": x.image,
                        "ports": str(x.ports) if x.ports else "",
                        "env": str(x.env) if x.env else "",
                        "resources": str(x.resources) if x.resources else "",
                        "volumemounts": str(x.volume_mounts) if x.volume_mounts else ""
                    } for x in ds.spec.template.spec.containers) if ds.spec.template and ds.spec.template.spec.containers else list()
                }

            self.log.write("GET", "Kube DaemonSet Data Import is completed.")

            self.ds_data = ds_data
        except Exception as e:
            self.log.write("Error", str(e))

    def get_kube_rs_data(self, api):
        try:
            replicasets = api.list_replica_set_for_all_namespaces()
            rs_data = dict()

            for rs in replicasets.items:
                rs_data[rs.metadata.uid] = {
                    "nsid": 0,
                    "name": rs.metadata.name,
                    "uid": rs.metadata.uid,
                    "starttime": utils.datetime_to_timestampz(rs.metadata.creation_timestamp),
                    "replicas": utils.nvl_zero(rs.status.replicas),
                    "fullylabeledrs": utils.nvl_zero(rs.status.fully_labeled_replicas),
                    "readyrs": utils.nvl_zero(rs.status.ready_replicas),
                    "availablers": utils.nvl_zero(rs.status.available_replicas),
                    "observedgen": utils.nvl_zero(rs.status.observed_generation),
                    "selector": utils.dict_to_str(rs.spec.selector.match_labels),
                    "refkind": rs.metadata.owner_references[0].kind if rs.metadata.owner_references else "",
                    "refid": 0,                    
                    "refuid": rs.metadata.owner_references[0].uid if rs.metadata.owner_references else 0,
                    "nsname": rs.metadata.namespace,
                    "containers": list() if rs.metadata.owner_references else list({
                        "kind": "ReplicaSet",
                        "uid": rs.metadata.uid,
                        "name": x.name,
                        "image": x.image,
                        "ports": str(x.ports) if x.ports else "",
                        "env": str(x.env) if x.env else "",
                        "resources": str(x.resources) if x.resources else "",
                        "volumemounts": str(x.volume_mounts) if x.volume_mounts else ""
                    } for x in rs.spec.template.spec.containers) if rs.spec.template and rs.spec.template.spec.containers else list()
                }

            self.log.write("GET", "Kube ReplicaSet Data Import is completed.")

            self.rs_data = rs_data                
        except Exception as e:
            self.log.write("Error", str(e))

    def get_kube_deploy_data(self, api):
        try:
            deployments = api.list_deployment_for_all_namespaces()
            deploy_data = dict()

            for deploy in deployments.items:
                deploy_data[deploy.metadata.uid] = {
                    "nsid": 0,
                    "name": deploy.metadata.name,
                    "uid": deploy.metadata.uid,
                    "starttime": utils.datetime_to_timestampz(deploy.metadata.creation_timestamp),
                    "serviceaccount": deploy.spec.template.spec.service_account,
                    "replicas": utils.nvl_zero(deploy.status.replicas),
                    "updatedrs": utils.nvl_zero(deploy.status.updated_replicas),
                    "readyrs": utils.nvl_zero(deploy.status.ready_replicas),
                    "availablers": utils.nvl_zero(deploy.status.available_replicas),
                    "observedgen": utils.nvl_zero(deploy.status.observed_generation),
                    "selector": utils.dict_to_str(deploy.spec.selector.match_labels),
                    "nsname": deploy.metadata.namespace,
                    "containers": list({
                        "kind": "Deployment",
                        "uid": deploy.metadata.uid,
                        "name": x.name,
                        "image": x.image,
                        "ports": str(x.ports) if x.ports else "",
                        "env": str(x.env) if x.env else "",
                        "resources": str(x.resources) if x.resources else "",
                        "volumemounts": str(x.volume_mounts) if x.volume_mounts else ""
                    } for x in deploy.spec.template.spec.containers) if deploy.spec.template and deploy.spec.template.spec.containers else list()
                }

            self.log.write("GET", "Kube Deployment Data Import is completed.")

            self.deploy_data = deploy_data                
        except Exception as e:
            self.log.write("Error", str(e))

    def get_kube_sts_data(self, api):
        try:
            statefulsets = api.list_stateful_set_for_all_namespaces()
            sts_data = dict()

            for sts in statefulsets.items:
                sts_data[sts.metadata.uid] = {
                    "nsid": 0,
                    "name": sts.metadata.name,
                    "uid": sts.metadata.uid,
                    "starttime": utils.datetime_to_timestampz(sts.metadata.creation_timestamp),
                    "serviceaccount": sts.spec.template.spec.service_account,
                    "replicas": utils.nvl_zero(sts.status.replicas),
                    "readyrs": utils.nvl_zero(sts.status.ready_replicas),
                    "availablers": utils.nvl_zero(sts.status.available_replicas),
                    "selector": utils.dict_to_str(sts.spec.selector.match_labels),
                    "nsname": sts.metadata.namespace,
                    "containers": list({
                        "kind": "StatefulSet",
                        "uid": sts.metadata.uid,
                        "name": x.name,
                        "image": x.image,
                        "ports": str(x.ports) if x.ports else "",
                        "env": str(x.env) if x.env else "",
                        "resources": str(x.resources) if x.resources else "",
                        "volumemounts": str(x.volume_mounts) if x.volume_mounts else ""
                    } for x in sts.spec.template.spec.containers) if sts.spec.template and sts.spec.template.spec.containers else list()
                }

            self.log.write("GET", "Kube StatefulSet Data Import is completed.")

            self.sts_data = sts_data                
        except Exception as e:
            self.log.write("Error", str(e))