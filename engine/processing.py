import csv
import json
from engine import stmt, utils
from collections import OrderedDict
from datetime import date, datetime, timedelta

class Processing:
    def __init__(self, log, db, system_var):
        # Pre-define data (여기에서의 Key는 Primary와 같은 Key값이 아니라 dictionary의 key-value의 key를 뜻함)
        self.manager_id = 0
        self.cluster_id = 0
        self.ref_container_list = list()
        self.ref_container_query = list()

        self.namespace_query_dict = dict()       # Key: UID
        self.svc_query_dict = dict()             # Key: UID
        self.deploy_query_dict = dict()          # Key: UID
        self.ds_query_dict = dict()              # Key: UID
        self.rs_query_dict = dict()              # Key: UID
        self.sts_query_dict = dict()             # Key: UID
        self.node_query_dict = dict()            # Key: Nodename
        self.node_sysco_query_dict = dict()      # Key: Nodeid
        self.pod_query_dict = dict()             # Key: UID
        self.pod_container_query_dict = dict()   # Key: Podid
        self.pod_device_query_dict = dict()      # Key: Devicetype

        self.log = log
        self.db = db
        self.system_var = system_var
        self.ref_process_flag = False
        self.schema_obj = {
            "reference": dict(),
            "metric": dict(),
            "index": dict()
        }

    def set_kube_data(self, kubedata, basic_info):
        # Kube Data variables
        self.namespace_list = kubedata.ns_data
        self.node_list = kubedata.node_data
        self.pod_list = kubedata.pod_data
        self.svc_list = kubedata.svc_data
        self.deploy_list = kubedata.deploy_data
        self.sts_list = kubedata.sts_data
        self.ds_list = kubedata.ds_data
        self.rs_list = kubedata.rs_data

        self.node_metric_list = kubedata.node_metric_data
        self.pod_metric_list = kubedata.pod_metric_data

        self.cluster_name = kubedata.cluster_name
        self.cluster_address = kubedata.cluster_address
        self.lt_interval = kubedata.lt_interval

        self.manager_name = basic_info["manager_name"]
        self.manager_ip = basic_info["manager_ip"]

    def input_tableinfo(self, name, cursor, conn, ontunetime=0):
        ontunetime = self.get_ontunetime(cursor) if ontunetime == 0 else ontunetime
        cursor.execute(stmt.SELECT_TABLEINFO_TABLENAME.format(name))
        result = cursor.fetchone()

        exec_stmt = stmt.UPDATE_TABLEINFO.format(ontunetime, name) if result[0] == 1 else stmt.INSERT_TABLEINFO.format(ontunetime, name)
        cursor.execute(exec_stmt)
        conn.commit()
        self.log.write("PUT", f"{name} data is added in kubetableinfo table.")

    def get_ontunetime(self, cursor):
        cursor.execute(stmt.SELECT_ONTUNEINFO)
        result = cursor.fetchone()
        return result[0] if result else 0

    def get_agenttime(self, cursor):
        cursor.execute(stmt.SELECT_ONTUNEINFO_BIAS)
        result = cursor.fetchone()
        return result[0]+(result[1]*60) if result else 0

    def get_api_podid(self, metric_uid):
        podid = 0
        if metric_uid in self.pod_query_dict:
            podid = self.pod_query_dict[metric_uid]["_podid"]
        else:
            pod_annotation_query_dict = dict(filter(lambda x: x[1]["_annotationuid"] == metric_uid, self.pod_query_dict.items()))
            for p in pod_annotation_query_dict:
                podid = pod_annotation_query_dict[p]["_podid"]
                break

        return podid

    def get_api_uid(self, metric_uid):
        if metric_uid in self.pod_query_dict:
            return metric_uid
        else:
            pod_annotation_query_dict = dict(filter(lambda x: x[1]["_annotationuid"] == metric_uid, self.pod_query_dict.items()))
            for p in pod_annotation_query_dict:
                return p

            return None

    def check_ontune_schema(self):
        # Load onTune Schema
        schema_object = {
            "reference": dict(),
            "metric": dict(),
            "index": dict()
        }
        with open('schema.csv', 'r', newline='') as csvfile:
            reader = csv.reader(csvfile, delimiter=",")
            for row in reader:
                (schema_type, obj_name) = row[:2]

                if obj_name not in schema_object[schema_type]:
                    schema_object[schema_type][obj_name] = list()

                properties = list(filter(lambda x: x != "", row[2:]))
                schema_object[schema_type][obj_name].append(properties)

        with self.db.get_resource_rdb() as (cursor, _, conn):
            # Check Reference Tables
            for obj_name in schema_object["reference"]:
                properties = schema_object["reference"][obj_name]
                cursor.execute(stmt.SELECT_PG_TABLES_TABLENAME_COUNT_REF.format(obj_name))
                result = cursor.fetchone()

                if result[0] == 1:
                    self.log.write("GET", f"Reference table {obj_name} is checked.")
                else:
                    self.log.write("GET", f"Reference table {obj_name} doesn't exist. now it will be created.")
                    creation_prefix = "create table if not exists"
                    column_properties = ",".join(list(" ".join(x) for x in properties))
                    table_creation_statement = f"{creation_prefix} {obj_name} ({column_properties});"
                    cursor.execute(table_creation_statement)
                    conn.commit()
                    self.log.write("PUT", f"Reference table {obj_name} creation is completed.")

                    self.input_tableinfo(obj_name, cursor, conn)
            
            # Check Metric Tables
            for obj_name in schema_object["metric"]:
                properties = schema_object["metric"][obj_name]
                table_postfix = f"_{datetime.now().strftime('%y%m%d')}00"
                cursor.execute(stmt.SELECT_PG_TABLES_TABLENAME_COUNT_MET.format(obj_name, table_postfix))
                result = cursor.fetchone()

                if result[0] == 2:
                    self.log.write("GET", f"Realtime/avg{obj_name}{table_postfix} metric tables are checked.")
                else:
                    # Metric Table Creation
                    self.log.write("GET", f"Realtime/avg{obj_name}{table_postfix} metric tables doesn't exist. now they will be created.")
                    creation_prefix = "create table if not exists"
                    column_properties = ",".join(list(" ".join(x) for x in properties))

                    for table_prefix in ('realtime','avg'):
                        full_table_name = f"{table_prefix}{obj_name}{table_postfix}"
                        table_creation_statement = f"{creation_prefix} {full_table_name} ({column_properties});"
                        cursor.execute(table_creation_statement)
                        conn.commit()
                        self.log.write("PUT", f"Metric table {full_table_name} creation is completed.")

                        # Metric Table Index Creation
                        index_properties = ",".join(schema_object["index"][obj_name][0])
                        index_creation_statement = f"create index if not exists i{full_table_name} on public.{full_table_name} using btree ({index_properties});"
                        cursor.execute(index_creation_statement)
                        conn.commit()
                        self.log.write("PUT", f"Metric table index i{full_table_name} creation is completed.")

                        self.input_tableinfo(full_table_name, cursor, conn)

        self.schema_obj = schema_object

    def update_reference_tables(self):
        self.ref_process_flag = True

        with self.db.get_resource_rdb() as (cursor, cur_dict, conn):
            self.update_manager_info(cursor, conn)
            self.update_cluster_info(cursor, conn)
            self.update_namespace_info(cursor, cur_dict, conn)
            self.update_node_info(cursor, cur_dict, conn)
            self.update_node_systemcontainer_info(cursor, cur_dict, conn)
            self.set_ref_container_list()
            self.update_service_info(cursor, cur_dict, conn)
            self.update_deployment_info(cursor, cur_dict, conn)
            self.update_statefulset_info(cursor, cur_dict, conn)
            self.update_daemonset_info(cursor, cur_dict, conn)
            self.update_replicaset_info(cursor, cur_dict, conn)
            self.update_pod_info(cursor, cur_dict, conn)
            self.update_ref_container_info(cursor, cur_dict, conn)
            self.update_pod_container_info(cursor, cur_dict, conn)
            self.update_pod_device_info(cursor, cur_dict, conn)

    def update_metric_tables(self):
        with self.db.get_resource_rdb() as (cursor, _, conn):
            self.update_lastrealtimeperf_table(cursor, conn)
            self.update_realtime_table(cursor, conn)
            self.update_average_table(cursor, conn)


    def update_manager_info(self, cursor, conn):
        if not self.ref_process_flag:
            return False

        try:
            cursor.execute(stmt.SELECT_MANAGERINFO_IP.format(self.manager_ip))
            result = cursor.fetchone()
            self.manager_id = result[0]
        except:            
            column_data = utils.insert_columns_ref(self.schema_obj, "kubemanagerinfo")
            value_data = utils.insert_values([self.manager_name, self.manager_name, self.manager_ip])
            cursor.execute(stmt.INSERT_TABLE.format("kubemanagerinfo", column_data, value_data))
            conn.commit()
            self.input_tableinfo("kubemanagerinfo", cursor, conn)
            self.log.write("PUT", f"Kubemanagerinfo insertion is completed - {self.manager_ip}")

            cursor.execute(stmt.SELECT_MANAGERINFO_IP.format(self.manager_ip))
            result = cursor.fetchone()
            self.manager_id = result[0]

        if not self.manager_id:
            self.log.write("GET", "Kubemanagerinfo has an error. Put data process is stopped.")
            self.ref_process_flag = False

    def update_cluster_info(self, cursor, conn):
        if not self.ref_process_flag:
            return False

        try:
            cursor.execute(stmt.SELECT_CLUSTERINFO_IP_MGRID.format(self.cluster_address, self.manager_id))
            result = cursor.fetchone()
            self.cluster_id = result[0]
        except:
            column_data = utils.insert_columns_ref(self.schema_obj, "kubeclusterinfo")
            value_data = utils.insert_values([self.manager_id, self.cluster_name, self.cluster_name, self.cluster_address])
            cursor.execute(stmt.INSERT_TABLE.format("kubeclusterinfo", column_data, value_data))
            conn.commit()
            self.input_tableinfo("kubeclusterinfo", cursor, conn)
            self.log.write("PUT", f"Kubeclusterinfo insertion is completed - {self.cluster_address}")

            cursor.execute(stmt.SELECT_CLUSTERINFO_IP_MGRID.format(self.cluster_address, self.manager_id))
            result = cursor.fetchone()
            self.cluster_id = result[0]

        if not self.cluster_id:
            self.log.write("GET", "Kubeclusterinfo has an error. Put data process is stopped.")
            self.ref_process_flag = False

    def update_namespace_info(self, cursor, cur_dict, conn):
        if not self.ref_process_flag:
            return False

        try:       
            cur_dict.execute(stmt.SELECT_NAMESPACEINFO_CLUSTERID.format(self.cluster_id))
            self.namespace_query_dict = dict({x["_nsname"]:x for x in cur_dict.fetchall()})
        except:
            pass
            
        try:
            new_namespace_list = list(filter(lambda x: x['name'] not in self.namespace_query_dict.keys(), self.namespace_list))
            old_namespace_list = dict(filter(lambda x: x[0] not in list(x['name'] for x in self.namespace_list), self.namespace_query_dict.items()))
            old_ns_id_list = list(str(x[1]["_nsid"]) for x in old_namespace_list.items())
            
            # New Namespace Insertion
            for new_ns in new_namespace_list:
                column_data = utils.insert_columns_ref(self.schema_obj, "kubensinfo")
                value_data = utils.insert_values([self.cluster_id, new_ns['name'], new_ns['status'], 1])
                cursor.execute(stmt.INSERT_TABLE.format("kubensinfo", column_data, value_data))
                conn.commit()
                self.input_tableinfo("kubensinfo", cursor, conn)
                self.log.write("PUT", f"Kubensinfo insertion is completed - {new_ns}")

            # Old Namespace Update
            if len(old_ns_id_list) > 0:
                cursor.execute(stmt.UPDATE_ENABLED.format("kubensinfo", "_nsid", ",".join(old_ns_id_list)))
                conn.commit()
                self.input_tableinfo("kubensinfo", cursor, conn)

            # New NS ID Update
            try:
                cur_dict.execute(stmt.SELECT_NAMESPACEINFO_CLUSTERID.format(self.cluster_id))
                result = cur_dict.fetchall()
                self.namespace_query_dict = dict({x["_nsname"]:x for x in result})
            except:
                pass
        except Exception as e:
            conn.rollback()
            self.log.write("GET", f"Kubenamespaceinfo has an error. Put data process is stopped. - {str(e)}")
            self.ref_process_flag = False

    def update_node_info(self, cursor, cur_dict, conn):
        if not self.ref_process_flag:
            return False

        try:
            cur_dict.execute(stmt.SELECT_NODEINFO_CLUSTERID.format(self.cluster_id))
            node_query_dict = dict({x["_nodename"]:x for x in cur_dict.fetchall()})
        except:
            pass

        # Namespace, Nodesysco, Pod 등의 정보는 과거 정보는 enabled=0으로 갱신, 신규 정보는 insert하도록하나,
        # Node 정보는 추가로 기존 정보의 변경사항에 대해서 Update하는 부분이 추가되므로 프로세스도 달라짐
        try:
            for node in self.node_list:
                if node in node_query_dict:
                    if self.node_list[node]["uid"] != node_query_dict[node]["_nodeuid"]:
                        update_data = utils.update_values(self.schema_obj, "kubenodeinfo", self.node_list[node])
                        cursor.execute(stmt.UPDATE_TABLE.format("kubenodeinfo", update_data, "_nodeid", node_query_dict[node]["_nodeid"]))
                        conn.commit()
                        self.input_tableinfo("kubenodeinfo", cursor, conn)
                        self.log.write("PUT", f"Kubenodeinfo information is updated - {node}")

                else:
                    column_data = utils.insert_columns_ref(self.schema_obj, "kubenodeinfo")
                    value_data = utils.insert_values([self.manager_id, self.cluster_id] + list(self.node_list[node].values()))
                    cursor.execute(stmt.INSERT_TABLE.format("kubenodeinfo", column_data, value_data))
                    conn.commit()
                    self.input_tableinfo("kubenodeinfo", cursor, conn)
                    self.log.write("PUT", f"Kubenodeinfo insertion is completed - {node}")

            old_node_list = dict(filter(lambda x: x[0] not in self.node_list, node_query_dict.items()))
            old_node_id_list = list(str(x[1]["_nodeid"]) for x in old_node_list.items())

            # Old Node Update
            if len(old_node_id_list) > 0:
                cursor.execute(stmt.UPDATE_ENABLED.format("kubenodeinfo", "_nodeid", ",".join(old_node_id_list)))
                conn.commit()
                self.input_tableinfo("kubenodeinfo", cursor, conn)
                self.log.write("PUT", f"Kubenodeinfo enabled state is updated - {','.join(old_node_id_list)}")

            # New Node ID Update
            try:
                cur_dict.execute(stmt.SELECT_NODEINFO_CLUSTERID.format(self.cluster_id))
                result = cur_dict.fetchall()
                self.node_query_dict = dict({x["_nodename"]:x for x in result})
            except:
                pass
        except Exception as e:
            conn.rollback()
            self.log.write("GET", f"Kubenodeinfo has an error. Put data process is stopped. - {str(e)}")
            self.ref_process_flag = False

        if not self.node_query_dict:
            self.log.write("GET", "Kubenodeinfo is empty. Put data process is stopped.")
            self.ref_process_flag = False

    def update_node_systemcontainer_info(self, cursor, cur_dict, conn):
        if not self.ref_process_flag:
            return False

        try:
            syscontainer_info_dict = dict({x[0]:list(y["name"] for y in x[1]["systemContainers"]) for x in self.node_metric_list.items()})
            syscontainer_info = list()
            sc_query_list = list()

            for node in syscontainer_info_dict:
                syscontainer_info.extend(list({"nodename":node, "containername": x} for x in syscontainer_info_dict[node]))

            try:
                nodeid_data = ",".join(list(str(self.node_query_dict[x]["_nodeid"]) for x in self.node_query_dict))
                cur_dict.execute(stmt.SELECT_NODE_SYSCONTAINER_NODEID.format(nodeid_data))
                sc_query_list = list(dict(x) for x in cur_dict.fetchall())
            except:
                pass

            new_node_sysco_list = list(filter(lambda x: [x["nodename"], x["containername"]] not in list([y["_nodename"],y["_containername"]] for y in sc_query_list), syscontainer_info))
            old_node_sysco_list = list(filter(lambda x: [x["_nodename"], x["_containername"]] not in list([y["nodename"],y["containername"]] for y in syscontainer_info), sc_query_list))

            for sysco in new_node_sysco_list:
                nodeid = self.node_query_dict[sysco["nodename"]]["_nodeid"]
                column_data = utils.insert_columns_ref(self.schema_obj, "kubenodesyscoinfo")
                value_data = utils.insert_values([nodeid, sysco["containername"], 1])
                cursor.execute(stmt.INSERT_TABLE.format("kubenodesyscoinfo", column_data, value_data))
                conn.commit()
                self.input_tableinfo("kubenodesyscoinfo", cursor, conn)
                self.log.write("PUT", f"Kubenodesyscoinfo insertion is completed - {sysco['nodename']} / {sysco['containername']}")

            if old_node_sysco_list:
                old_node_sysco_id_list = list(str(x["_syscontainerid"]) for x in old_node_sysco_list)

                if len(old_node_sysco_id_list) > 0:
                    cursor.execute(stmt.UPDATE_ENABLED.format("kubenodesyscoinfo","_syscontainerid",",".join(old_node_sysco_id_list)))
                    conn.commit()
                    self.input_tableinfo("kubenodesyscoinfo", cursor, conn)
                    self.log.write("PUT", f"Kubenodesyscoinfo enabled state is updated - {','.join(self.old_node_id_list)}")

            # New Node System Container info Update
            try:
                nodeid_data = ",".join(list(str(self.node_query_dict[x]["_nodeid"]) for x in self.node_query_dict))
                cur_dict.execute(stmt.SELECT_NODE_SYSCONTAINER_NODEID.format(nodeid_data))
                result = cur_dict.fetchall()

                for row in result:
                    nodeid = row["_nodeid"]
                    if nodeid not in self.node_sysco_query_dict:
                        self.node_sysco_query_dict[nodeid] = list()

                    self.node_sysco_query_dict[nodeid].append(row)
            except:
                pass
        except Exception as e:
            conn.rollback()
            self.log.write("GET", f"Kubenodesystemcontainer info has an error. Put data process is stopped. - {str(e)}")
            self.ref_process_flag = False

    def set_ref_container_list(self):
        if not self.ref_process_flag:
            return False

        pod_container_list = list()
        for pod in self.pod_list:
            pod_containers = self.pod_list[pod].pop("containers")                
            pod_container_list.extend(pod_containers)
        pod_container_refined_list = list(map(dict, OrderedDict.fromkeys(tuple(sorted(x.items())) for x in pod_container_list)))
        self.ref_container_list.extend(pod_container_refined_list)

        deploy_container_list = list()
        for deploy in self.deploy_list:
            deploy_containers = self.deploy_list[deploy].pop("containers")
            deploy_container_list.extend(deploy_containers)
        deploy_container_refined_list = list(map(dict, OrderedDict.fromkeys(tuple(sorted(x.items())) for x in deploy_container_list)))
        self.ref_container_list.extend(deploy_container_refined_list)

        sts_container_list = list()
        for sts in self.sts_list:
            sts_containers = self.sts_list[sts].pop("containers")
            sts_container_list.extend(sts_containers)
        sts_container_refined_list = list(map(dict, OrderedDict.fromkeys(tuple(sorted(x.items())) for x in sts_container_list)))
        self.ref_container_list.extend(sts_container_refined_list)

        ds_container_list = list()
        for ds in self.ds_list:
            ds_containers = self.ds_list[ds].pop("containers")
            ds_container_list.extend(ds_containers)
        ds_container_refined_list = list(map(dict, OrderedDict.fromkeys(tuple(sorted(x.items())) for x in ds_container_list)))
        self.ref_container_list.extend(ds_container_refined_list)

        rs_container_list = list()
        for rs in self.rs_list:
            rs_containers = self.rs_list[rs].pop("containers")
            rs_container_list.extend(rs_containers)
        rs_container_refined_list = list(map(dict, OrderedDict.fromkeys(tuple(sorted(x.items())) for x in rs_container_list)))
        self.ref_container_list.extend(rs_container_refined_list)

    def update_service_info(self, cursor, cur_dict, conn):
        if not self.ref_process_flag:
            return False

        try:
            cur_dict.execute(stmt.SELECT_SVCINFO_CLUSTERID.format(self.cluster_id))
            self.svc_query_dict = dict({x["_uid"]:x for x in cur_dict.fetchall()})
        except:
            pass

        try:
            old_svc_list = dict(filter(lambda x: x[0] not in self.svc_list, self.svc_query_dict.items()))
            new_svc_list = dict(filter(lambda x: x[0] not in self.svc_query_dict, self.svc_list.items()))
            old_svc_id_list = list(str(x[1]["_svcid"]) for x in old_svc_list.items())

            # New SVC Insertion
            for new_svc in new_svc_list:
                svc_data = dict(new_svc_list[new_svc])
                svc_ns_name = svc_data.pop("nsname")
                svc_data["nsid"] = self.namespace_query_dict[svc_ns_name]["_nsid"]
                column_data = utils.insert_columns_ref(self.schema_obj, "kubesvcinfo")
                value_data = utils.insert_values(list(svc_data.values())+[1])
                cursor.execute(stmt.INSERT_TABLE.format("kubesvcinfo", column_data, value_data))
                conn.commit()
                self.input_tableinfo("kubesvcinfo", cursor, conn)
                self.log.write("PUT", f"Kubesvcinfo insertion is completed - {new_svc}")

            # Old SVC Update
            if len(old_svc_id_list) > 0:
                cursor.execute(stmt.UPDATE_ENABLED.format("kubesvcinfo", "_svcid", ",".join(old_svc_id_list)))
                conn.commit()
                self.input_tableinfo("kubesvcinfo", cursor, conn)

            # New SVC ID Update
            try:
                cur_dict.execute(stmt.SELECT_SVCINFO_CLUSTERID.format(self.cluster_id))
                result = cur_dict.fetchall()
                self.svc_query_dict = dict({x["_uid"]:x for x in result})
            except:
                pass
        except Exception as e:
            conn.rollback()
            self.log.write("GET", f"Kubesvcinfo has an error. Put data process is stopped. - {str(e)}")
            self.ref_process_flag = False

    def update_deployment_info(self, cursor, cur_dict, conn):
        if not self.ref_process_flag:
            return False            

        try:
            cur_dict.execute(stmt.SELECT_DEPLOYINFO_CLUSTERID.format(self.cluster_id))
            self.deploy_query_dict = dict({x["_uid"]:x for x in cur_dict.fetchall()})
        except:
            pass

        try:
            old_deploy_list = dict(filter(lambda x: x[0] not in self.deploy_list, self.deploy_query_dict.items()))
            new_deploy_list = dict(filter(lambda x: x[0] not in self.deploy_query_dict, self.deploy_list.items()))
            old_deploy_id_list = list(str(x[1]["_deployid"]) for x in old_deploy_list.items())

            # New deploy Insertion
            for new_deploy in new_deploy_list:
                deploy_data = dict(new_deploy_list[new_deploy])
                deploy_ns_name = deploy_data.pop("nsname")
                deploy_data["nsid"] = self.namespace_query_dict[deploy_ns_name]["_nsid"]

                column_data = utils.insert_columns_ref(self.schema_obj, "kubedeployinfo")
                value_data = utils.insert_values(list(deploy_data.values())+[1])
                cursor.execute(stmt.INSERT_TABLE.format("kubedeployinfo", column_data, value_data))
                conn.commit()

                self.input_tableinfo("kubedeployinfo", cursor, conn)
                self.log.write("PUT", f"Kubedeployinfo insertion is completed - {new_deploy}")

            # Old deploy Update
            if len(old_deploy_id_list) > 0:
                cursor.execute(stmt.UPDATE_ENABLED.format("kubedeployinfo", "_deployid", ",".join(old_deploy_id_list)))
                conn.commit()
                self.input_tableinfo("kubedeployinfo", cursor, conn)

            # New deploy ID Update
            try:
                cur_dict.execute(stmt.SELECT_DEPLOYINFO_CLUSTERID.format(self.cluster_id))
                result = cur_dict.fetchall()
                self.deploy_query_dict = dict({x["_uid"]:x for x in result})
            except:
                pass            
        except Exception as e:
            conn.rollback()
            self.log.write("GET", f"Kubedeployinfo has an error. Put data process is stopped. - {str(e)}")
            self.ref_process_flag = False

    def update_statefulset_info(self, cursor, cur_dict, conn):
        if not self.ref_process_flag:
            return False

        try:
            cur_dict.execute(stmt.SELECT_STSINFO_CLUSTERID.format(self.cluster_id))
            self.sts_query_dict = dict({x["_uid"]:x for x in cur_dict.fetchall()})
        except:
            pass

        try:
            old_sts_list = dict(filter(lambda x: x[0] not in self.sts_list, self.sts_query_dict.items()))
            new_sts_list = dict(filter(lambda x: x[0] not in self.sts_query_dict, self.sts_list.items()))
            old_sts_id_list = list(str(x[1]["_stsid"]) for x in old_sts_list.items())

            # New sts Insertion
            # Transaction 오류 발생, 처리 필요 차주에 합시다...
            # 오류:  현재 트랜잭션은 중지되어 있습니다. 이 트랜잭션을 종료하기 전까지는 모든 명령이 무시될 것입니다
            for new_sts in new_sts_list:
                sts_data = dict(new_sts_list[new_sts])
                sts_ns_name = sts_data.pop("nsname")
                sts_data["nsid"] = self.namespace_query_dict[sts_ns_name]["_nsid"]

                column_data = utils.insert_columns_ref(self.schema_obj, "kubestsinfo")
                value_data = utils.insert_values(list(sts_data.values())+[1])
                cursor.execute(stmt.INSERT_TABLE.format("kubestsinfo", column_data, value_data))
                conn.commit()

                self.input_tableinfo("kubestsinfo", cursor, conn)
                self.log.write("PUT", f"Kubestsinfo insertion is completed - {new_sts}")

            # Old sts Update
            if len(old_sts_id_list) > 0:
                cursor.execute(stmt.UPDATE_ENABLED.format("kubestsinfo", "_stsid", ",".join(old_sts_id_list)))
                conn.commit()
                self.input_tableinfo("kubestsinfo", cursor, conn)

            # New sts ID Update
            try:
                cur_dict.execute(stmt.SELECT_STSINFO_CLUSTERID.format(self.cluster_id))
                result = cur_dict.fetchall()
                self.sts_query_dict = dict({x["_uid"]:x for x in result})
            except:
                pass            
        except Exception as e:
            conn.rollback()
            self.log.write("GET", f"Kubestsinfo has an error. Put data process is stopped. - {str(e)}")
            self.ref_process_flag = False

    def update_daemonset_info(self, cursor, cur_dict, conn):
        if not self.ref_process_flag:
            return False

        try:
            cur_dict.execute(stmt.SELECT_DSINFO_CLUSTERID.format(self.cluster_id))
            self.ds_query_dict = dict({x["_uid"]:x for x in cur_dict.fetchall()})
        except:
            pass

        try:
            old_ds_list = dict(filter(lambda x: x[0] not in self.ds_list, self.ds_query_dict.items()))
            new_ds_list = dict(filter(lambda x: x[0] not in self.ds_query_dict, self.ds_list.items()))
            old_ds_id_list = list(str(x[1]["_dsid"]) for x in old_ds_list.items())

            # New ds Insertion
            for new_ds in new_ds_list:
                ds_data = dict(new_ds_list[new_ds])
                ds_ns_name = ds_data.pop("nsname")
                ds_data["nsid"] = self.namespace_query_dict[ds_ns_name]["_nsid"]

                column_data = utils.insert_columns_ref(self.schema_obj, "kubedsinfo")
                value_data = utils.insert_values(list(ds_data.values())+[1])
                cursor.execute(stmt.INSERT_TABLE.format("kubedsinfo", column_data, value_data))
                conn.commit()
                self.input_tableinfo("kubedsinfo", cursor, conn)
                self.log.write("PUT", f"Kubedsinfo insertion is completed - {new_ds}")

            # Old ds Update
            if len(old_ds_id_list) > 0:
                cursor.execute(stmt.UPDATE_ENABLED.format("kubedsinfo", "_dsid", ",".join(old_ds_id_list)))
                conn.commit()
                self.input_tableinfo("kubedsinfo", cursor, conn)

            # New ds ID Update
            try:
                cur_dict.execute(stmt.SELECT_DSINFO_CLUSTERID.format(self.cluster_id))
                result = cur_dict.fetchall()
                self.ds_query_dict = dict({x["_uid"]:x for x in result})
            except:
                pass
        except Exception as e:
            conn.rollback()
            self.log.write("GET", f"Kubedsinfo has an error. Put data process is stopped. - {str(e)}")
            self.ref_process_flag = False

    def update_replicaset_info(self, cursor, cur_dict, conn):
        if not self.ref_process_flag:
            return False

        try:
            cur_dict.execute(stmt.SELECT_RSINFO_CLUSTERID.format(self.cluster_id))
            self.rs_query_dict = dict({x["_uid"]:x for x in cur_dict.fetchall()})
        except:
            pass

        try:
            old_rs_list = dict(filter(lambda x: x[0] not in self.rs_list, self.rs_query_dict.items()))
            new_rs_list = dict(filter(lambda x: x[0] not in self.rs_query_dict, self.rs_list.items()))
            old_rs_id_list = list(str(x[1]["_rsid"]) for x in old_rs_list.items())

            # New rs Insertion
            for new_rs in new_rs_list:
                rs_data = dict(new_rs_list[new_rs])
                rs_ns_name = rs_data.pop("nsname")
                rs_ref_uid = rs_data.pop("refuid")
                rs_data["nsid"] = self.namespace_query_dict[rs_ns_name]["_nsid"]

                if rs_data["refkind"] == "Deployment":
                    rs_data["refid"] = self.deploy_query_dict[rs_ref_uid]["_deployid"]
                elif rs_data["refkind"] == "StatefulSet":
                    rs_data["refid"] = self.sts_query_dict[rs_ref_uid]["_stsid"]

                column_data = utils.insert_columns_ref(self.schema_obj, "kubersinfo")
                value_data = utils.insert_values(list(rs_data.values())+[1])
                cursor.execute(stmt.INSERT_TABLE.format("kubersinfo", column_data, value_data))
                conn.commit()
                self.input_tableinfo("kubersinfo", cursor, conn)
                self.log.write("PUT", f"Kubersinfo insertion is completed - {new_rs}")

            # Old rs Update
            if len(old_rs_id_list) > 0:
                cursor.execute(stmt.UPDATE_ENABLED.format("kubersinfo", "_rsid", ",".join(old_rs_id_list)))
                conn.commit()
                self.input_tableinfo("kubersinfo", cursor, conn)

            # New rs ID Update
            try:
                cur_dict.execute(stmt.SELECT_RSINFO_CLUSTERID.format(self.cluster_id))
                result = cur_dict.fetchall()
                self.rs_query_dict = dict({x["_uid"]:x for x in result})
            except:
                pass
        except Exception as e:
            conn.rollback()
            self.log.write("GET", f"Kubersinfo has an error. Put data process is stopped. - {str(e)}")
            self.ref_process_flag = False

    def update_pod_info(self, cursor, cur_dict, conn):
        if not self.ref_process_flag:
            return False

        try:
            cur_dict.execute(stmt.SELECT_PODINFO_CLUSTERID.format(self.cluster_id))
            self.pod_query_dict = dict({x["_uid"]:x for x in cur_dict.fetchall()})
        except:
            pass

        try:
            old_pod_list = dict(filter(lambda x: x[0] not in self.pod_list, self.pod_query_dict.items()))
            new_pod_list = dict(filter(lambda x: x[0] not in self.pod_query_dict, self.pod_list.items()))
            old_pod_id_list = list(str(x[1]["_podid"]) for x in old_pod_list.items())

            # New pod Insertion
            for new_pod in new_pod_list:
                pod_data = dict(new_pod_list[new_pod])
                pod_node_name = pod_data.pop("nodename")
                pod_ns_name = pod_data.pop("nsname")
                pod_ref_uid = pod_data.pop("refuid")

                pod_data["nsid"] = self.namespace_query_dict[pod_ns_name]["_nsid"] if pod_ns_name else 0
                pod_data["nodeid"] = self.node_query_dict[pod_node_name]["_nodeid"] if pod_node_name else 0

                if pod_data["nodeid"] == 0:
                    continue

                if pod_data["refkind"] == "DaemonSet":
                    pod_data["refid"] = self.ds_query_dict[pod_ref_uid]["_dsid"]
                elif pod_data["refkind"] == "ReplicaSet":
                    pod_data["refid"] = self.rs_query_dict[pod_ref_uid]["_rsid"]
                elif pod_data["refkind"] == "StatefulSet":
                    pod_data["refid"] = self.sts_query_dict[pod_ref_uid]["_stsid"]

                column_data = utils.insert_columns_ref(self.schema_obj, "kubepodinfo")
                value_data = utils.insert_values(list(pod_data.values())+[1])
                cursor.execute(stmt.INSERT_TABLE.format("kubepodinfo", column_data, value_data))
                conn.commit()

                self.input_tableinfo("kubepodinfo", cursor, conn)
                self.log.write("PUT", f"Kubepodinfo insertion is completed - {new_pod}")

            # Old pod Update
            if len(old_pod_id_list) > 0:
                cursor.execute(stmt.UPDATE_ENABLED.format("kubepodinfo", "_podid", ",".join(old_pod_id_list)))
                conn.commit()
                self.input_tableinfo("kubepodinfo", cursor, conn)

            # New pod ID Update
            try:
                cur_dict.execute(stmt.SELECT_PODINFO_CLUSTERID.format(self.cluster_id))
                result = cur_dict.fetchall()
                self.pod_query_dict = dict({x["_uid"]:x for x in result})
            except:
                pass

            if not self.pod_query_dict:
                self.log.write("GET", "Kubepodinfo is empty. Put data process is stopped.")
                self.ref_process_flag = False
        except Exception as e:
            conn.rollback()
            self.log.write("GET", f"Kubepodinfo has an error. Put data process is stopped. - {str(e)}")
            self.ref_process_flag = False


    def update_ref_container_info(self, cursor, cur_dict, conn):
        if not self.ref_process_flag:
            return False

        try:
            cur_dict.execute(stmt.SELECT_REF_CONTAINERINFO_CLUSTERID.format(self.cluster_id))
            self.ref_container_query = cur_dict.fetchall()
        except:
            pass

        try:
            def rc_exist_condition_new(rc):
                if "kind" not in rc:
                    return True
                else:
                    return not (rc["kind"],rc["name"],rc["image"]) in list(
                        (x["_refobjkind"],x["_refcontainername"],x["_image"]) for x in self.ref_container_query
                    )

            def rc_exist_condition_old(rc):
                if "_refobjkind" not in rc:
                    return True
                else:
                    return not (rc["_refobjkind"],rc["_refcontainername"],rc["_image"]) in list(
                        (x["kind"],x["name"],x["image"]) for x in self.ref_container_list
                    )

            old_rc_list = list(filter(lambda x: rc_exist_condition_old(x), self.ref_container_query))
            new_rc_list = list(filter(lambda x: rc_exist_condition_new(x), self.ref_container_list))
            old_rc_id_list = list(str(x["_refcontainerid"]) for x in old_rc_list)

            # New rc Insertion
            for new_rc in new_rc_list:
                rc_data = {
                    "objkind": new_rc["kind"],
                    "objid": 0,
                    "clusterid": self.cluster_id,
                    "name": new_rc["name"],
                    "image": new_rc["image"],
                    "ports": json.dumps(new_rc["ports"])[1:-1].replace("'", '"'),
                    "env": json.dumps(new_rc["env"])[1:-1].replace("'", '"'),
                    "resources": json.dumps(new_rc["resources"])[1:-1].replace("'", '"'),
                    "volumemounts": json.dumps(new_rc["volumemounts"])[1:-1].replace("'", '"')
                }
                rc_obj_uid = new_rc["uid"]

                if rc_data["objkind"] == "Deployment":
                    rc_data["objid"] = self.deploy_query_dict[rc_obj_uid]["_deployid"]
                elif rc_data["objkind"] == "StatefulSet":
                    rc_data["objid"] = self.sts_query_dict[rc_obj_uid]["_stsid"]
                elif rc_data["objkind"] == "DaemonSet":
                    rc_data["objid"] = self.ds_query_dict[rc_obj_uid]["_dsid"]
                elif rc_data["objkind"] == "ReplicaSet":
                    rc_data["objid"] = self.rs_query_dict[rc_obj_uid]["_rsid"]
                elif rc_data["objkind"] == "Pod":
                    rc_data["objid"] = self.pod_query_dict[rc_obj_uid]["_podid"]

                column_data = utils.insert_columns_ref(self.schema_obj, "kuberefcontainerinfo")
                value_data = utils.insert_values(list(rc_data.values())+[1])
                
                cursor.execute(stmt.INSERT_TABLE.format("kuberefcontainerinfo", column_data, value_data))
                conn.commit()

                self.input_tableinfo("kuberefcontainerinfo", cursor, conn)
                self.log.write("PUT", f"kuberefcontainerinfo insertion is completed - {new_rc}")

            # Old rc Update
            if len(old_rc_id_list) > 0:
                cursor.execute(stmt.UPDATE_ENABLED.format("kuberefcontainerinfo", "_refcontainerid", ",".join(old_rc_id_list)))
                conn.commit()
                self.input_tableinfo("kuberefcontainerinfo", cursor, conn)

            # New rc ID Update
            try:
                cur_dict.execute(stmt.SELECT_REF_CONTAINERINFO_CLUSTERID.format(self.cluster_id))
                self.ref_container_query = cur_dict.fetchall()
            except:
                pass
        except Exception as e:
            conn.rollback()
            self.log.write("GET", f"kuberefcontainerinfo has an error. Put data process is stopped. - {str(e)}")
            self.ref_process_flag = False

    def update_pod_container_info(self, cursor, cur_dict, conn):
        if not self.ref_process_flag:
            return False

        try:
            container_info = list()
            container_query_list = list()
            
            containerinfo_dict = dict({x[0]:dict({
                y["podRef"]["uid"]:y["containers"] for y in x[1]
            }) for x in self.pod_metric_list.items()})

            for node in containerinfo_dict:
                for pod in containerinfo_dict[node]:
                    container_info.extend(list({
                        "name": x["name"],
                        "starttime": datetime.strptime(x["startTime"],"%Y-%m-%dT%H:%M:%S%z"),
                        "pod": pod,
                        "node": node
                    } for x in containerinfo_dict[node][pod]))

            try:
                nodeid_data = ",".join(list(str(self.node_query_dict[x]["_nodeid"]) for x in self.node_query_dict))
                cur_dict.execute(stmt.SELECT_CONTAINERINFO_NODEID.format(nodeid_data))
                container_query_list = list(dict(x) for x in cur_dict.fetchall())
            except:
                pass

            new_container_list = list(filter(lambda x: [x["node"],self.get_api_uid(x["pod"]),x["name"]] not in list([y["_nodename"],y["_poduid"],y["_containername"]] for y in container_query_list), container_info))

            for container in new_container_list:
                # Pod ID는 Stats API에서 조회되는 UID가 Kubernetes API에서는 UID / Annotation - UID 둘 중 하나로 입력되므로 매핑작업이 선행되어야 함
                podid = self.get_api_podid(self.get_api_uid(container["pod"]))

                column_data = utils.insert_columns_ref(self.schema_obj, "kubecontainerinfo")
                value_data = utils.insert_values([podid, container["name"], utils.datetime_to_timestampz(container["starttime"])])

                cursor.execute(stmt.INSERT_TABLE.format("kubecontainerinfo", column_data, value_data))
                conn.commit()
                self.input_tableinfo("kubecontainerinfo", cursor, conn)
                self.log.write("PUT", f"Kubecontainerinfo insertion is completed - {podid} / {container['name']}")

            # New Pod Container info Update
            try:
                nodeid_data = ",".join(list(str(self.node_query_dict[x]["_nodeid"]) for x in self.node_query_dict))
                cur_dict.execute(stmt.SELECT_CONTAINERINFO_NODEID.format(nodeid_data))
                result = cur_dict.fetchall()

                for row in result:
                    podid = row["_podid"]
                    if podid not in self.pod_container_query_dict:
                        self.pod_container_query_dict[podid] = list()

                    self.pod_container_query_dict[podid].append(row)
            except:
                pass
        except Exception as e:
            conn.rollback()
            self.log.write("GET", f"Kubecontainerinfo has an error. Put data process is stopped. - {str(e)}")
            self.ref_process_flag = False

    def update_pod_device_info(self, cursor, cur_dict, conn):
        if not self.ref_process_flag:
            return False

        try:
            device_type_set = {'network','volume'}
            device_info = dict()
            deviceinfo_query_list = dict()

            for device_type in device_type_set:
                device_info[device_type] = list()

                try:
                    cursor.execute(stmt.SELECT_PODDEVICEINFO_DEVICETYPE.format(device_type))
                    deviceinfo_query_list[device_type] = cursor.fetchall()
                except:
                    deviceinfo_query_list[device_type] = list()

            netdeviceinfo_dict = dict({x[0]:dict({
                y["podRef"]["uid"]:list(
                    z["name"] for z in y["network"]["interfaces"]
                ) for y in x[1] if "network" in y
            }) for x in self.pod_metric_list.items()})

            for node in netdeviceinfo_dict:
                for pod in netdeviceinfo_dict[node]:
                    device_info['network'].extend(netdeviceinfo_dict[node][pod])

            voldeviceinfo_dict = dict({x[0]:dict({
                y["podRef"]["uid"]:list(
                    z["name"] for z in y["volume"]
                ) for y in x[1] if "volume" in y
            }) for x in self.pod_metric_list.items()})

            for node in voldeviceinfo_dict:
                for pod in voldeviceinfo_dict[node]:
                    device_info['volume'].extend(voldeviceinfo_dict[node][pod])

            device_info_set = dict({x[0]:set(x[1]) for x in device_info.items()})
            new_dev_info_set = dict({x:set(filter(lambda y: y not in list(z[1] for z in deviceinfo_query_list[x]), device_info_set[x])) for x in device_type_set})

            for devtype in device_type_set:
                for devinfo in new_dev_info_set[devtype]:
                    column_data = utils.insert_columns_ref(self.schema_obj, "kubepoddeviceinfo")
                    value_data = utils.insert_values([devinfo, devtype])
                    cursor.execute(stmt.INSERT_TABLE.format("kubepoddeviceinfo", column_data, value_data))
                    conn.commit()
                    self.input_tableinfo("kubepoddeviceinfo", cursor, conn)
                    self.log.write("PUT", f"Kubepoddeviceinfo insertion is completed - {devtype} / {devinfo}")

                # New Pod Device info Update
                try:
                    cur_dict.execute(stmt.SELECT_PODDEVICEINFO_DEVICETYPE.format(devtype))
                    self.pod_device_query_dict[devtype] = cur_dict.fetchall()
                except:
                    pass
        except Exception as e:
            conn.rollback()
            self.log.write("GET", f"Kubedeviceinfo has an error. Put data process is stopped. - {str(e)}")
            self.ref_process_flag = False

    def update_lastrealtimeperf_table(self, cursor, conn):
        if not self.ref_process_flag:
            return False

        try:
            for node in self.node_query_dict:
                node_data = self.node_metric_list[node]

                network_prev_cum_usage = self.system_var.get_network_metric("lastrealtimeperf", node)                
                network_cum_usage = sum(list(x["rxBytes"]+x["txBytes"] for x in node_data["network"]["interfaces"]))
                network_usage = network_cum_usage - network_prev_cum_usage if network_prev_cum_usage else 0
                ontunetime = self.get_ontunetime(cursor)

                node_perf = [
                    self.node_query_dict[node]["_nodeid"],
                    ontunetime,                    
                    utils.calculate('cpu_usage_percent', [node_data["cpu"]["usageNanoCores"], self.node_list[node]["cpucount"]]),
                    utils.calculate('memory_used_percent', node_data["memory"]),
                    utils.calculate('memory_swap_percent', node_data["memory"]),
                    utils.calculate('memory_size', node_data["memory"]),
                    node_data["memory"]["rssBytes"],
                    utils.calculate('network', [network_usage]),
                    utils.calculate('fs_usage_percent', node_data["fs"]),
                    utils.calculate('fs_total_size', node_data["fs"]),
                    utils.calculate('fs_inode_usage_percent', node_data["fs"]),
                    utils.calculate('fs_usage_percent', node_data["runtime"]["imageFs"]),
                    node_data["rlimit"]["curproc"]
                ]

                self.system_var.set_network_metric("lastrealtimeperf", node, network_cum_usage)

                column_data = utils.insert_columns_metric(self.schema_obj, "kubelastrealtimeperf")
                value_data = utils.insert_values(node_perf)

                cursor.execute(stmt.DELETE_LASTREALTIMEPERF.format(self.node_query_dict[node]["_nodeid"]))
                cursor.execute(stmt.INSERT_TABLE.format("kubelastrealtimeperf", column_data, value_data))
                conn.commit()
                self.input_tableinfo("kubelastrealtimeperf", cursor, conn, ontunetime)
                self.log.write("PUT", f"Kubelastrealtimeperf update is completed - {self.node_query_dict[node]['_nodeid']}")
        except Exception as e:
            conn.rollback()
            self.log.write("GET", f"Kubelastrealtimeperf has an error. Put data process is stopped. - {str(e)}")
            return False

    def update_realtime_table(self, cursor, conn):
        if not self.ref_process_flag:
            return False

        ontunetime = self.get_ontunetime(cursor)
        agenttime = self.get_agenttime(cursor)

        try:
            table_postfix = f"_{datetime.now().strftime('%y%m%d')}00"

            for node in self.node_query_dict:
                # Setting node data
                node_data = self.node_metric_list[node]
                nodeid = self.node_query_dict[node]["_nodeid"]

                network_prev_cum_usage = self.system_var.get_network_metric("nodeperf", node)
                network_cum_usage = [
                    utils.calculate('network', [sum(list(x["rxBytes"]+x["txBytes"] for x in node_data["network"]["interfaces"]))]),
                    utils.calculate('network', [sum(list(x["rxBytes"] for x in node_data["network"]["interfaces"]))]),
                    utils.calculate('network', [sum(list(x["txBytes"] for x in node_data["network"]["interfaces"]))]),
                    sum(list(x["rxErrors"] for x in node_data["network"]["interfaces"])),
                    sum(list(x["txErrors"] for x in node_data["network"]["interfaces"]))
                ]
                network_usage = list(network_cum_usage[x] - network_prev_cum_usage[x] if network_prev_cum_usage else 0 for x in range(len(network_cum_usage)))

                # Insert nodeperf metric data
                realtime_nodeperf = [
                    nodeid,
                    ontunetime,
                    agenttime,
                    utils.calculate('cpu_usage_percent', [node_data["cpu"]["usageNanoCores"], self.node_list[node]["cpucount"]]),
                    utils.calculate('memory_used_percent', node_data["memory"]),
                    utils.calculate('memory_swap_percent', node_data["memory"]),
                    utils.calculate('memory_size', node_data["memory"]),
                    node_data["memory"]["rssBytes"]
                ] + network_usage + [
                    utils.calculate('fs_usage_percent', node_data["fs"]),
                    utils.calculate('fs_total_size', node_data["fs"]),
                    utils.calculate('fs_free_size', node_data["fs"]),
                    utils.calculate('fs_inode_usage_percent', node_data["fs"]),
                    utils.calculate('fs_inode_total_size', node_data["fs"]),
                    utils.calculate('fs_inode_free_size', node_data["fs"]),
                    utils.calculate('fs_usage_percent', node_data["runtime"]["imageFs"]),
                    node_data["rlimit"]["maxpid"],
                    node_data["rlimit"]["curproc"]
                ]

                self.system_var.set_network_metric("nodeperf", node, network_cum_usage)

                table_name = f"realtimekubenodeperf{table_postfix}"
                column_data = utils.insert_columns_metric(self.schema_obj, "kubenodeperf")
                value_data = utils.insert_values(realtime_nodeperf)

                cursor.execute(stmt.INSERT_TABLE.format(table_name, column_data, value_data))
                conn.commit()
                self.input_tableinfo(table_name, cursor, conn, ontunetime)
                self.log.write("PUT", f"{table_name} update is completed - {self.node_query_dict[node]['_nodeid']}")

                # Insert node system container metric data
                sysco_data = dict({x["name"]: {
                    "cpu": x["cpu"],
                    "memory": x["memory"]
                } for x in node_data["systemContainers"]})

                for sysco_query_data in self.node_sysco_query_dict[nodeid]:
                    containername = sysco_query_data["_containername"]

                    realtime_node_sysco = [
                        nodeid,
                        sysco_query_data["_syscontainerid"],
                        ontunetime,
                        agenttime,
                        utils.calculate('cpu_usage_percent', [sysco_data[containername]["cpu"]["usageNanoCores"], self.node_list[node]["cpucount"]]) if "usageNanoCores" in sysco_data[containername]["cpu"] else 0,
                        utils.calculate('memory_used_percent', sysco_data[containername]["memory"]),
                        utils.calculate('memory_swap_percent', sysco_data[containername]["memory"]),
                        utils.calculate('memory_size', sysco_data[containername]["memory"]),
                        sysco_data[containername]["memory"]["rssBytes"] if "rssBytes" in sysco_data[containername]["memory"] else 0
                    ]

                    table_name = f"realtimekubenodesysco{table_postfix}"
                    column_data = utils.insert_columns_metric(self.schema_obj, "kubenodesysco")
                    value_data = utils.insert_values(realtime_node_sysco)

                    cursor.execute(stmt.INSERT_TABLE.format(table_name, column_data, value_data))
                    conn.commit()
                    self.input_tableinfo(table_name, cursor, conn, ontunetime)

                self.log.write("PUT", f"{table_name} update is completed - {self.node_query_dict[node]['_nodeid']}")

                # Setting node data
                pod_data = self.pod_metric_list[node]
                for pod in pod_data:
                    uid = pod["podRef"]["uid"]
                    podid = self.get_api_podid(uid)

                    network_prev_cum_usage = self.system_var.get_network_metric("podperf", podid)
                    network_cum_usage = [
                        utils.calculate('network', [sum(list(x["rxBytes"]+x["txBytes"] for x in pod["network"]["interfaces"]))]) if "network" in pod and "interfaces" in pod["network"] else 0,
                        utils.calculate('network', [sum(list(x["rxBytes"] for x in pod["network"]["interfaces"]))]) if "network" in pod and "interfaces" in pod["network"] else 0,
                        utils.calculate('network', [sum(list(x["txBytes"] for x in pod["network"]["interfaces"]))]) if "network" in pod and "interfaces" in pod["network"] else 0,
                        sum(list(x["rxErrors"] for x in pod["network"]["interfaces"])) if "network" in pod and "interfaces" in pod["network"] else 0,
                        sum(list(x["txErrors"] for x in pod["network"]["interfaces"])) if "network" in pod and "interfaces" in pod["network"] else 0,
                    ]
                    network_usage = list(network_cum_usage[x] - network_prev_cum_usage[x] if network_prev_cum_usage else 0 for x in range(len(network_cum_usage)))

                    # Insert pod metric data
                    realtime_podperf = [
                        podid,
                        ontunetime,
                        agenttime,
                        utils.calculate('cpu_usage_percent', [pod["cpu"]["usageNanoCores"], self.node_list[node]["cpucount"]]) if "cpu" in pod and "usageNanoCores" in pod["cpu"] else 0,
                        utils.calculate('memory_used_percent', pod["memory"]),
                        utils.calculate('memory_swap_percent', pod["memory"]),
                        utils.calculate('memory_size', pod["memory"]),
                        pod["memory"]["rssBytes"] if "memory" in pod and "rssBytes" in pod["memory"] else 0
                    ] + network_usage + [
                        sum(int(x["usedBytes"]) for x in pod["volume"]) if "volume" in pod and "usedBytes" in pod["volume"][0] else 0,
                        sum(int(x["inodesUsed"]) for x in pod["volume"]) if "volume" in pod and "inodesUsed" in pod["volume"][0] else 0,
                        pod["ephemeral-storage"]["usedBytes"],
                        pod["ephemeral-storage"]["inodesUsed"],
                        pod["process_stats"]["process_count"] if "ephemeral-storage" in pod and "process_count" in pod["process_stats"] else 0
                    ]

                    self.system_var.set_network_metric("podperf", podid, network_cum_usage)

                    table_name = f"realtimekubepodperf{table_postfix}"
                    column_data = utils.insert_columns_metric(self.schema_obj, "kubepodperf")
                    value_data = utils.insert_values(realtime_podperf)

                    cursor.execute(stmt.INSERT_TABLE.format(table_name, column_data, value_data))
                    conn.commit()
                    self.input_tableinfo(table_name, cursor, conn, ontunetime)
                    self.log.write("PUT", f"{table_name} update is completed - {uid}")

                    # Insert pod container metric data
                    for pod_container in pod["containers"]:
                        realtime_containerperf = [
                            list(filter(lambda x: x["_containername"] == pod_container["name"], list(self.pod_container_query_dict[podid])))[0]["_containerid"],
                            ontunetime,
                            agenttime,
                            utils.calculate('cpu_usage_percent', [pod_container["cpu"]["usageNanoCores"], self.node_list[node]["cpucount"]]) if "cpu" in pod_container and "usageNanoCores" in pod_container["cpu"] else 0,
                            utils.calculate('memory_used_percent', pod_container["memory"]),
                            utils.calculate('memory_swap_percent', pod_container["memory"]),
                            utils.calculate('memory_size', pod_container["memory"]),
                            pod_container["memory"]["rssBytes"] if "memory" in pod_container and "rssBytes" in pod_container["memory"] else 0,
                            pod_container["rootfs"]["usedBytes"],
                            pod_container["rootfs"]["inodesUsed"],
                            pod_container["logs"]["usedBytes"],
                            pod_container["logs"]["inodesUsed"]
                        ]

                        table_name = f"realtimekubecontainerperf{table_postfix}"
                        column_data = utils.insert_columns_metric(self.schema_obj, "kubecontainerperf")
                        value_data = utils.insert_values(realtime_containerperf)

                        cursor.execute(stmt.INSERT_TABLE.format(table_name, column_data, value_data))
                        conn.commit()
                        self.input_tableinfo(table_name, cursor, conn, ontunetime)

                    self.log.write("PUT", f"{table_name} update is completed - {uid}")

                    # Insert pod device metric data
                    if "network" in pod and "interfaces" in pod["network"]:
                        for pod_network in pod["network"]["interfaces"]:
                            deviceid = list(filter(lambda x: x["_devicename"] == pod_network["name"], list(self.pod_device_query_dict["network"])))[0]["_deviceid"]
                            podnet_key = f"{podid}_{deviceid}"

                            network_prev_cum_usage = self.system_var.get_network_metric("podnet", podnet_key)
                            network_cum_usage = [
                                utils.calculate('network', [pod_network["rxBytes"] + pod_network["txBytes"]]),
                                utils.calculate('network', [pod_network["rxBytes"]]),
                                utils.calculate('network', [pod_network["txBytes"]]),
                                pod_network["rxErrors"],
                                pod_network["txErrors"]                                                        
                            ]
                            network_usage = list(network_cum_usage[x] - network_prev_cum_usage[x] if network_prev_cum_usage else 0 for x in range(len(network_cum_usage)))

                            realtime_podnet = [
                                podid,
                                deviceid,
                                ontunetime,
                                agenttime
                            ] + network_usage

                            self.system_var.set_network_metric("podnet", podnet_key, network_cum_usage)

                            table_name = f"realtimekubepodnet{table_postfix}"
                            column_data = utils.insert_columns_metric(self.schema_obj, "kubepodnet")
                            value_data = utils.insert_values(realtime_podnet)

                            cursor.execute(stmt.INSERT_TABLE.format(table_name, column_data, value_data))
                            conn.commit()
                            self.input_tableinfo(table_name, cursor, conn, ontunetime)

                    if "volume" in pod:
                        for pod_volume in pod["volume"]:
                            realtime_podvol = [
                                podid,
                                list(filter(lambda x: x["_devicename"] == pod_volume["name"], list(self.pod_device_query_dict["volume"])))[0]["_deviceid"],
                                ontunetime,
                                agenttime,
                                pod_volume["usedBytes"],
                                pod_volume["inodesUsed"]
                            ]

                            table_name = f"realtimekubepodvol{table_postfix}"
                            column_data = utils.insert_columns_metric(self.schema_obj, "kubepodvol")
                            value_data = utils.insert_values(realtime_podvol)

                            cursor.execute(stmt.INSERT_TABLE.format(table_name, column_data, value_data))
                            conn.commit()
                            self.input_tableinfo(table_name, cursor, conn, ontunetime)

                    self.log.write("PUT", f"{table_name} update is completed - {uid}")

        except Exception as e:
            conn.rollback()
            self.log.write("GET", f"Kube realtime tables have an error. Put data process is stopped. - {str(e)}")
            self.ref_process_flag = False

    def insert_average_table(self, table_midfix, key_columns, cursor, conn):
        try:
            ontunetime = self.get_ontunetime(cursor)
            agenttime = self.get_agenttime(cursor)

            today_postfix = f"_{date.today().strftime('%y%m%d')}00"
            prev_postfix = f"_{(date.today() - timedelta(1)).strftime('%y%m%d')}00"
            lt_prev_ontunetime = ontunetime - self.lt_interval

            from_clause = str()
            table_st_prev_name = f"realtime{table_midfix}{prev_postfix}"
            table_st_name = f"realtime{table_midfix}{today_postfix}"
            table_lt_name = f"avg{table_midfix}{today_postfix}"

            # Yesterday table check
            cursor.execute(stmt.SELECT_PG_TABLES_TABLENAME_COUNT_MET.format(table_midfix, prev_postfix))
            result = cursor.fetchone()
            if result[0] > 0:
                from_clause = f"(select * from {table_st_prev_name} union all select * from {table_st_name}) t"
            else:
                from_clause = table_st_name

            # Between으로 하지 않는 이유는 lt_prev_ontunetime보다 GTE가 아니라 GT가 되어야 하기 때문
            select_clause = utils.select_average_columns(self.schema_obj, table_midfix, key_columns)
            where_clause = f"_ontunetime > {lt_prev_ontunetime} and _ontunetime <= {ontunetime}"
            group_clause = f" group by {','.join(key_columns)}"
            
            cursor.execute(stmt.SELECT_TABLE.format(select_clause, from_clause, where_clause, group_clause))
            result = cursor.fetchall()

            for row in result:
                column_data = utils.insert_columns_metric(self.schema_obj, table_midfix)
                value_data = utils.insert_values(list(row[:len(key_columns)]) + [ontunetime, agenttime] + list(row[len(key_columns):]))
                
                cursor.execute(stmt.INSERT_TABLE.format(table_lt_name, column_data, value_data))
                conn.commit()
                self.input_tableinfo(table_lt_name, cursor, conn, ontunetime)

            self.log.write("PUT", f"{table_lt_name} update is completed - {ontunetime}")
        except Exception as e:
            conn.rollback()
            self.log.write("GET", f"Kube average tables have an error. Put data process is stopped. - {str(e)}")

    def update_average_table(self, cursor, conn):
        if not self.ref_process_flag:
            return False

        if self.system_var.get_duration() % self.lt_interval == 0:
            self.insert_average_table("kubenodeperf", ["_nodeid"], cursor, conn)
            self.insert_average_table("kubenodesysco", ["_nodeid","_syscontainerid"], cursor, conn)
            self.insert_average_table("kubepodperf", ["_podid"], cursor, conn)
            self.insert_average_table("kubecontainerperf", ["_containerid"], cursor, conn)
            self.insert_average_table("kubepodnet", ["_podid","_deviceid"], cursor, conn)
            self.insert_average_table("kubepodvol", ["_podid","_deviceid"], cursor, conn)