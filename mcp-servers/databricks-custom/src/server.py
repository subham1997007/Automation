"""
Databricks Custom MCP Server
============================
All 44 Databricks tools in ONE file — fully owned, modify as needed.
Built on top of databricks-tools-core (the underlying function library).

Tools:
  SQL          : execute_sql, execute_sql_multi, manage_warehouse,
                 get_table_stats_and_schema, get_volume_folder_details
  Compute      : execute_code, manage_cluster, manage_sql_warehouse, list_compute
  Unity Catalog: manage_uc_objects, manage_uc_grants, manage_uc_storage,
                 manage_uc_connections, manage_uc_tags, manage_uc_security_policies,
                 manage_uc_monitors, manage_uc_sharing, manage_metric_views
  Jobs         : manage_jobs, manage_job_runs
  Pipelines    : manage_pipeline, manage_pipeline_run
  Vector Search: manage_vs_endpoint, manage_vs_index, query_vs_index, manage_vs_data
  Serving      : manage_serving_endpoint
  Genie        : manage_genie, ask_genie
  Agent Bricks : manage_ka, manage_mas
  Lakebase     : manage_lakebase_database, manage_lakebase_branch,
                 manage_lakebase_sync, generate_lakebase_credential
  Apps         : manage_app
  Dashboards   : manage_dashboard
  Files        : manage_volume_files, manage_workspace_files
  Workspace    : manage_workspace
  User         : get_current_user
  PDF          : generate_and_upload_pdf
  Manifest     : list_tracked_resources, delete_tracked_resource
"""

import asyncio
import functools
import inspect
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastMCP init — wrap sync tools in threads so stdio transport stays responsive
# ---------------------------------------------------------------------------

mcp = FastMCP("databricks-custom")


def _wrap_sync_in_thread(fn):
    @functools.wraps(fn)
    async def async_wrapper(**kwargs):
        return await asyncio.to_thread(fn, **kwargs)
    return async_wrapper


_original_tool = mcp.tool


def _patched_tool(*args, **kwargs):
    def decorator(fn):
        if not inspect.iscoroutinefunction(fn):
            fn = _wrap_sync_in_thread(fn)
        if args and callable(args[0]):
            return _original_tool(fn)
        return _original_tool(*args, **kwargs)(fn)
    if args and callable(args[0]):
        fn = args[0]
        if not inspect.iscoroutinefunction(fn):
            fn = _wrap_sync_in_thread(fn)
        return _original_tool(fn)
    return decorator


mcp.tool = _patched_tool

# ---------------------------------------------------------------------------
# Imports from databricks-tools-core
# ---------------------------------------------------------------------------

# SQL
from databricks_tools_core.sql import (
    execute_sql as _execute_sql,
    execute_sql_multi as _execute_sql_multi,
    list_warehouses as _list_warehouses,
    get_best_warehouse as _get_best_warehouse,
    get_table_stats_and_schema as _get_table_stats_and_schema,
    get_volume_folder_details as _get_volume_folder_details,
    TableStatLevel,
)

# Compute
from databricks_tools_core.compute import (
    list_clusters as _list_clusters,
    get_best_cluster as _get_best_cluster,
    start_cluster as _start_cluster,
    get_cluster_status as _get_cluster_status,
    execute_databricks_command as _execute_databricks_command,
    run_file_on_databricks as _run_file_on_databricks,
    run_code_on_serverless as _run_code_on_serverless,
    NoRunningClusterError,
    create_cluster as _create_cluster,
    modify_cluster as _modify_cluster,
    terminate_cluster as _terminate_cluster,
    delete_cluster as _delete_cluster,
    list_node_types as _list_node_types,
    list_spark_versions as _list_spark_versions,
    create_sql_warehouse as _create_sql_warehouse,
    modify_sql_warehouse as _modify_sql_warehouse,
    delete_sql_warehouse as _delete_sql_warehouse,
)

# Unity Catalog
from databricks_tools_core.unity_catalog import (
    list_catalogs as _list_catalogs, get_catalog as _get_catalog,
    create_catalog as _create_catalog, update_catalog as _update_catalog,
    delete_catalog as _delete_catalog,
    list_schemas as _list_schemas, get_schema as _get_schema,
    create_schema as _create_schema, update_schema as _update_schema,
    delete_schema as _delete_schema,
    list_volumes as _list_volumes, get_volume as _get_volume,
    create_volume as _create_volume, update_volume as _update_volume,
    delete_volume as _delete_volume,
    list_functions as _list_functions, get_function as _get_function,
    delete_function as _delete_function,
    grant_privileges as _grant_privileges, revoke_privileges as _revoke_privileges,
    get_grants as _get_grants, get_effective_grants as _get_effective_grants,
    list_storage_credentials as _list_storage_credentials,
    get_storage_credential as _get_storage_credential,
    create_storage_credential as _create_storage_credential,
    update_storage_credential as _update_storage_credential,
    delete_storage_credential as _delete_storage_credential,
    validate_storage_credential as _validate_storage_credential,
    list_external_locations as _list_external_locations,
    get_external_location as _get_external_location,
    create_external_location as _create_external_location,
    update_external_location as _update_external_location,
    delete_external_location as _delete_external_location,
    list_connections as _list_connections, get_connection as _get_connection,
    create_connection as _create_connection, update_connection as _update_connection,
    delete_connection as _delete_connection,
    create_foreign_catalog as _create_foreign_catalog,
    set_tags as _set_tags, unset_tags as _unset_tags, set_comment as _set_comment,
    query_table_tags as _query_table_tags, query_column_tags as _query_column_tags,
    create_security_function as _create_security_function,
    set_row_filter as _set_row_filter, drop_row_filter as _drop_row_filter,
    set_column_mask as _set_column_mask, drop_column_mask as _drop_column_mask,
    create_monitor as _create_monitor, get_monitor as _get_monitor,
    run_monitor_refresh as _run_monitor_refresh,
    list_monitor_refreshes as _list_monitor_refreshes, delete_monitor as _delete_monitor,
    list_shares as _list_shares, get_share as _get_share,
    create_share as _create_share, add_table_to_share as _add_table_to_share,
    remove_table_from_share as _remove_table_from_share, delete_share as _delete_share,
    grant_share_to_recipient as _grant_share_to_recipient,
    revoke_share_from_recipient as _revoke_share_from_recipient,
    list_recipients as _list_recipients, get_recipient as _get_recipient,
    create_recipient as _create_recipient, rotate_recipient_token as _rotate_recipient_token,
    delete_recipient as _delete_recipient,
    list_providers as _list_providers, get_provider as _get_provider,
    list_provider_shares as _list_provider_shares,
    create_metric_view as _create_metric_view, alter_metric_view as _alter_metric_view,
    drop_metric_view as _drop_metric_view, describe_metric_view as _describe_metric_view,
    query_metric_view as _query_metric_view, grant_metric_view as _grant_metric_view,
)

# Jobs
from databricks_tools_core.jobs import (
    list_jobs as _list_jobs, get_job as _get_job,
    find_job_by_name as _find_job_by_name, create_job as _create_job,
    update_job as _update_job, delete_job as _delete_job,
    run_job_now as _run_job_now, repair_run as _repair_run,
    get_run as _get_run, get_run_output as _get_run_output,
    cancel_run as _cancel_run, list_runs as _list_runs,
    wait_for_run as _wait_for_run,
)

# Pipelines
from databricks_tools_core.spark_declarative_pipelines.pipelines import (
    create_pipeline as _create_pipeline, get_pipeline as _get_pipeline,
    update_pipeline as _update_pipeline, delete_pipeline as _delete_pipeline,
    start_update as _start_update, get_update as _get_update,
    stop_pipeline as _stop_pipeline, get_pipeline_events as _get_pipeline_events,
    create_or_update_pipeline as _create_or_update_pipeline,
    find_pipeline_by_name as _find_pipeline_by_name,
)

# Vector Search
from databricks_tools_core.vector_search import (
    create_vs_endpoint as _create_vs_endpoint, get_vs_endpoint as _get_vs_endpoint,
    list_vs_endpoints as _list_vs_endpoints, delete_vs_endpoint as _delete_vs_endpoint,
    create_vs_index as _create_vs_index, get_vs_index as _get_vs_index,
    list_vs_indexes as _list_vs_indexes, delete_vs_index as _delete_vs_index,
    sync_vs_index as _sync_vs_index, query_vs_index as _query_vs_index,
    upsert_vs_data as _upsert_vs_data, delete_vs_data as _delete_vs_data,
    scan_vs_index as _scan_vs_index,
)

# Serving
from databricks_tools_core.serving import (
    get_serving_endpoint_status as _get_serving_endpoint_status,
    query_serving_endpoint as _query_serving_endpoint,
    list_serving_endpoints as _list_serving_endpoints,
)

# Agent Bricks / Genie
from databricks_tools_core.agent_bricks import AgentBricksManager, get_tile_example_queue
from databricks_tools_core.auth import get_workspace_client
from databricks_tools_core.identity import get_default_tags, with_description_footer

# Lakebase (provisioned)
from databricks_tools_core.lakebase import (
    create_lakebase_instance as _create_instance,
    get_lakebase_instance as _get_instance,
    list_lakebase_instances as _list_instances,
    update_lakebase_instance as _update_instance,
    delete_lakebase_instance as _delete_instance,
    generate_lakebase_credential as _generate_provisioned_credential,
    create_lakebase_catalog as _create_lakebase_catalog,
    create_synced_table as _create_synced_table,
    delete_synced_table as _delete_synced_table,
)

# Lakebase (autoscale)
from databricks_tools_core.lakebase_autoscale import (
    create_project as _create_project, get_project as _get_project,
    list_projects as _list_projects, update_project as _update_project,
    delete_project as _delete_project,
    create_branch as _create_branch, list_branches as _list_branches,
    update_branch as _update_branch, delete_branch as _delete_branch,
    create_endpoint as _create_endpoint, list_endpoints as _list_endpoints,
    update_endpoint as _update_endpoint,
    generate_credential as _generate_autoscale_credential,
)

# Apps
from databricks_tools_core.apps import (
    create_app as _create_app,
    deploy_app as _deploy_app,
    get_app as _get_app,
    list_apps as _list_apps,
    delete_app as _delete_app,
    get_app_logs as _get_app_logs,
)

# Dashboards
from databricks_tools_core.aibi_dashboards import (
    create_or_update_dashboard as _create_or_update_dashboard,
    get_dashboard as _get_dashboard,
    list_dashboards as _list_dashboards,
    trash_dashboard as _trash_dashboard,
    publish_dashboard as _publish_dashboard,
    unpublish_dashboard as _unpublish_dashboard,
)

# Files
from databricks_tools_core.file import (
    upload_to_workspace as _upload_to_workspace,
    delete_from_workspace as _delete_from_workspace,
)
from databricks_tools_core.unity_catalog import (
    list_volume_files as _list_volume_files,
    upload_to_volume as _upload_to_volume,
    download_from_volume as _download_from_volume,
    delete_from_volume as _delete_from_volume,
    create_volume_directory as _create_volume_directory,
    get_volume_file_metadata as _get_volume_file_metadata,
)

# User / Workspace
from databricks_tools_core.auth import get_workspace_client, get_current_username as _get_current_username

# PDF
from databricks_tools_core.pdf import generate_and_upload_pdf as _generate_and_upload_pdf

# ---------------------------------------------------------------------------
# Simple in-memory manifest (resource tracker)
# ---------------------------------------------------------------------------

_MANIFEST: Dict[str, List[Dict]] = {}
_DELETERS: Dict[str, Any] = {}


def _track_resource(resource_type: str, name: str, resource_id: str):
    _MANIFEST.setdefault(resource_type, [])
    for r in _MANIFEST[resource_type]:
        if r["resource_id"] == resource_id:
            return
    _MANIFEST[resource_type].append({"name": name, "resource_id": resource_id, "type": resource_type})


def _remove_resource(resource_type: str, resource_id: str):
    if resource_type in _MANIFEST:
        _MANIFEST[resource_type] = [r for r in _MANIFEST[resource_type] if r["resource_id"] != resource_id]


def _register_deleter(resource_type: str, fn):
    _DELETERS[resource_type] = fn


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _none_if_empty(v):
    return None if v == "" else v


def _to_dict(obj):
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(exclude_none=True)
    return vars(obj)


def _to_dict_list(items):
    return [_to_dict(i) for i in (items or [])]


def _fmt_md(rows: List[Dict]) -> str:
    if not rows:
        return "(no results)"
    cols = list(rows[0].keys())
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in rows:
        lines.append("| " + " | ".join("" if row.get(c) is None else str(row[c]).replace("|", "\\|") for c in cols) + " |")
    lines.append(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return "\n".join(lines)


def _auto_tag(object_type: str, full_name: str):
    for k, v in get_default_tags().items():
        try:
            _set_tags(object_type=object_type, full_name=full_name, tags={k: v})
        except Exception:
            pass


# ===========================================================================
# ── SQL TOOLS ───────────────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool(timeout=60)
def execute_sql(
    sql_query: str,
    warehouse_id: str = None,
    catalog: str = None,
    schema: str = None,
    timeout: int = 180,
    query_tags: str = None,
    output_format: str = "markdown",
) -> Union[str, List[Dict]]:
    """Execute SQL query on Databricks warehouse. Auto-selects warehouse if not provided.

    Use for SELECT/INSERT/UPDATE/table DDL. For catalog/schema/volume DDL, use manage_uc_objects.
    output_format: "markdown" (default, 50% smaller) or "json"."""
    rows = _execute_sql(sql_query=sql_query, warehouse_id=warehouse_id,
                        catalog=catalog, schema=schema, timeout=timeout, query_tags=query_tags)
    return rows if output_format == "json" else _fmt_md(rows)


@mcp.tool(timeout=120)
def execute_sql_multi(
    sql_content: str,
    warehouse_id: str = None,
    catalog: str = None,
    schema: str = None,
    timeout: int = 180,
    max_workers: int = 4,
    query_tags: str = None,
    output_format: str = "markdown",
) -> Dict:
    """Execute multiple SQL statements with dependency-aware parallelism. Independent queries run in parallel."""
    result = _execute_sql_multi(sql_content=sql_content, warehouse_id=warehouse_id,
                                catalog=catalog, schema=schema, timeout=timeout,
                                max_workers=max_workers, query_tags=query_tags)
    if output_format != "json" and "results" in result:
        for qr in result["results"].values():
            sample = qr.get("sample_results")
            if sample and isinstance(sample, list):
                qr["sample_results"] = _fmt_md(sample)
    return result


@mcp.tool(timeout=30)
def manage_warehouse(action: str = "get_best") -> Union[str, List, Dict]:
    """Manage SQL warehouses: list, get_best.

    Actions:
    - list: List all SQL warehouses. Returns: {warehouses: [...]}.
    - get_best: Get best available warehouse ID. Returns: {warehouse_id}."""
    act = action.lower()
    if act == "list":
        return {"warehouses": _list_warehouses()}
    if act == "get_best":
        wid = _get_best_warehouse()
        return {"warehouse_id": wid} if wid else {"warehouse_id": None, "error": "No available warehouses"}
    return {"error": f"Invalid action '{action}'. Valid: list, get_best"}


@mcp.tool(timeout=60)
def get_table_stats_and_schema(
    catalog: str,
    schema: str,
    table_names: List[str] = None,
    table_stat_level: str = "SIMPLE",
    warehouse_id: str = None,
) -> Dict:
    """Get schema and stats for tables. table_stat_level: NONE/SIMPLE/DETAILED. table_names: list or glob, None=all."""
    level = TableStatLevel[table_stat_level.upper()]
    result = _get_table_stats_and_schema(catalog=catalog, schema=schema,
                                         table_names=table_names, table_stat_level=level,
                                         warehouse_id=warehouse_id)
    return result.model_dump(exclude_none=True) if hasattr(result, "model_dump") else result


@mcp.tool(timeout=60)
def get_volume_folder_details(
    volume_path: str,
    format: str = "parquet",
    table_stat_level: str = "SIMPLE",
    warehouse_id: str = None,
) -> Dict:
    """Get schema/stats for data files in Volume folder. format: parquet/csv/json/delta/file."""
    level = TableStatLevel[table_stat_level.upper()]
    result = _get_volume_folder_details(volume_path=volume_path, format=format,
                                        table_stat_level=level, warehouse_id=warehouse_id)
    return result.model_dump(exclude_none=True) if hasattr(result, "model_dump") else result


# ===========================================================================
# ── COMPUTE TOOLS ───────────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool
def execute_code(
    code: str = None,
    file_path: str = None,
    compute_type: str = "auto",
    cluster_id: str = None,
    context_id: str = None,
    language: str = "python",
    timeout: int = None,
    destroy_context_on_completion: bool = False,
    workspace_path: str = None,
    run_name: str = None,
    job_extra_params: Dict = None,
) -> Dict:
    """Execute code on Databricks via serverless or cluster compute.

    Modes: auto (default), serverless (~30s cold start), cluster (persistent via context_id).
    file_path: Run local .py/.scala/.sql/.r file. workspace_path: Save as notebook (omit=ephemeral).
    job_extra_params: Extra params for serverless e.g. {"environments": [...]}.
    Returns: {success, output, error, cluster_id, context_id} or {run_id, run_url}."""
    code = _none_if_empty(code)
    file_path = _none_if_empty(file_path)
    cluster_id = _none_if_empty(cluster_id)
    context_id = _none_if_empty(context_id)
    language = _none_if_empty(language) or "python"
    workspace_path = _none_if_empty(workspace_path)

    if not code and not file_path:
        return {"success": False, "error": "Either 'code' or 'file_path' must be provided."}

    if compute_type == "auto":
        if cluster_id or context_id:
            compute_type = "cluster"
        elif language and language.lower() in ("scala", "r"):
            compute_type = "cluster"
        else:
            compute_type = "serverless"

    if file_path:
        if compute_type == "serverless":
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    code = f.read()
            except Exception as e:
                return {"success": False, "error": str(e)}
        else:
            try:
                result = _run_file_on_databricks(
                    file_path=file_path, cluster_id=cluster_id, context_id=context_id,
                    language=language if language != "python" else None,
                    timeout=timeout or 600, destroy_context_on_completion=destroy_context_on_completion,
                    workspace_path=workspace_path)
                return result.to_dict()
            except NoRunningClusterError as e:
                return {"success": False, "error": str(e), "suggestions": e.suggestions}

    if compute_type == "serverless":
        result = _run_code_on_serverless(
            code=code, language=language, timeout=timeout or 1800, run_name=run_name,
            cleanup=workspace_path is None, workspace_path=workspace_path,
            job_extra_params=job_extra_params)
        return result.to_dict()

    try:
        result = _execute_databricks_command(
            code=code, cluster_id=cluster_id, context_id=context_id,
            language=language, timeout=timeout or 120,
            destroy_context_on_completion=destroy_context_on_completion)
        return result.to_dict()
    except NoRunningClusterError as e:
        return {"success": False, "error": str(e), "suggestions": e.suggestions}


@mcp.tool
def manage_cluster(
    action: str,
    cluster_id: str = None,
    name: str = None,
    num_workers: int = None,
    spark_version: str = None,
    node_type_id: str = None,
    autotermination_minutes: int = None,
    data_security_mode: str = None,
    spark_conf: str = None,
    autoscale_min_workers: int = None,
    autoscale_max_workers: int = None,
) -> Dict:
    """Create, modify, start, terminate, or delete a cluster.

    Actions: create (name required), modify (cluster_id required), start, terminate, get, delete.
    num_workers default 1. spark_conf: JSON string. Returns: {cluster_id, cluster_name, state, message}."""
    action = action.lower()
    cluster_id = _none_if_empty(cluster_id)
    name = _none_if_empty(name)

    if action == "create":
        if not name:
            return {"success": False, "error": "name required for create"}
        kwargs = {}
        if spark_version: kwargs["spark_version"] = spark_version
        if node_type_id: kwargs["node_type_id"] = node_type_id
        if data_security_mode: kwargs["data_security_mode"] = data_security_mode
        if spark_conf and spark_conf.strip(): kwargs["spark_conf"] = json.loads(spark_conf)
        if autoscale_min_workers is not None: kwargs["autoscale_min_workers"] = autoscale_min_workers
        if autoscale_max_workers is not None: kwargs["autoscale_max_workers"] = autoscale_max_workers
        return _create_cluster(name=name, num_workers=num_workers or 1,
                               autotermination_minutes=autotermination_minutes or 120, **kwargs)
    elif action == "modify":
        if not cluster_id: return {"success": False, "error": "cluster_id required for modify"}
        kwargs = {}
        if name: kwargs["name"] = name
        if num_workers is not None: kwargs["num_workers"] = num_workers
        if spark_version: kwargs["spark_version"] = spark_version
        if autotermination_minutes is not None: kwargs["autotermination_minutes"] = autotermination_minutes
        if spark_conf and spark_conf.strip(): kwargs["spark_conf"] = json.loads(spark_conf)
        return _modify_cluster(cluster_id=cluster_id, **kwargs)
    elif action == "start":
        if not cluster_id: return {"success": False, "error": "cluster_id required"}
        return _start_cluster(cluster_id)
    elif action == "terminate":
        if not cluster_id: return {"success": False, "error": "cluster_id required"}
        return _terminate_cluster(cluster_id)
    elif action == "delete":
        if not cluster_id: return {"success": False, "error": "cluster_id required"}
        return _delete_cluster(cluster_id)
    elif action == "get":
        if not cluster_id: return {"success": False, "error": "cluster_id required"}
        return _get_cluster_status(cluster_id)
    return {"success": False, "error": f"Unknown action: {action}"}


@mcp.tool
def manage_sql_warehouse(
    action: str,
    warehouse_id: str = None,
    name: str = None,
    size: str = None,
    min_num_clusters: int = None,
    max_num_clusters: int = None,
    auto_stop_mins: int = None,
    warehouse_type: str = None,
    enable_serverless: bool = None,
) -> Dict:
    """Create, modify, or delete a SQL warehouse.

    Actions: create (name required), modify (warehouse_id required), delete.
    size: "2X-Small" to "4X-Large". Returns: {warehouse_id, name, state, message}."""
    action = action.lower()
    warehouse_id = _none_if_empty(warehouse_id)
    name = _none_if_empty(name)

    if action == "create":
        if not name: return {"success": False, "error": "name required"}
        return _create_sql_warehouse(name=name, size=size or "Small",
                                     min_num_clusters=min_num_clusters or 1,
                                     max_num_clusters=max_num_clusters or 1,
                                     auto_stop_mins=auto_stop_mins or 120,
                                     warehouse_type=warehouse_type or "PRO",
                                     enable_serverless=enable_serverless if enable_serverless is not None else True)
    elif action == "modify":
        if not warehouse_id: return {"success": False, "error": "warehouse_id required"}
        kwargs = {k: v for k, v in dict(name=name, size=size, min_num_clusters=min_num_clusters,
                                         max_num_clusters=max_num_clusters, auto_stop_mins=auto_stop_mins).items() if v is not None}
        return _modify_sql_warehouse(warehouse_id=warehouse_id, **kwargs)
    elif action == "delete":
        if not warehouse_id: return {"success": False, "error": "warehouse_id required"}
        return _delete_sql_warehouse(warehouse_id)
    return {"success": False, "error": f"Unknown action: {action}"}


@mcp.tool
def list_compute(
    resource: str = "clusters",
    cluster_id: str = None,
    auto_select: bool = False,
) -> Dict:
    """List compute resources: clusters, node types, or spark versions.

    resource: "clusters" (default), "node_types", "spark_versions".
    cluster_id: Get specific cluster status. auto_select: Return best running cluster."""
    resource = resource.lower()
    cluster_id = _none_if_empty(cluster_id)
    if resource == "clusters":
        if cluster_id: return _get_cluster_status(cluster_id)
        if auto_select: return {"cluster_id": _get_best_cluster()}
        return {"clusters": _list_clusters()}
    elif resource == "node_types":
        return {"node_types": _list_node_types()}
    elif resource == "spark_versions":
        return {"spark_versions": _list_spark_versions()}
    return {"success": False, "error": f"Unknown resource: {resource}"}


# ===========================================================================
# ── UNITY CATALOG TOOLS ─────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool(timeout=60)
def manage_uc_objects(
    object_type: str, action: str,
    name: str = None, full_name: str = None,
    catalog_name: str = None, schema_name: str = None,
    comment: str = None, owner: str = None,
    storage_root: str = None, volume_type: str = None,
    storage_location: str = None, new_name: str = None,
    properties: Dict[str, str] = None,
    isolation_mode: str = None, force: bool = False,
) -> Dict:
    """Manage UC namespace objects: catalog/schema/volume/function.

    object_type: "catalog", "schema", "volume", or "function".
    action: "create", "get", "list", "update", "delete".
    full_name format: "catalog" or "catalog.schema" or "catalog.schema.object".
    Returns: list={items}, get/create/update=object details, delete={status}."""
    otype = object_type.lower()

    if otype == "catalog":
        if action == "create":
            result = _to_dict(_create_catalog(name=name, comment=comment,
                                               storage_root=storage_root, properties=properties))
            _auto_tag("catalog", name)
            _track_resource("catalog", name, result.get("name", name))
            return result
        elif action == "get": return _to_dict(_get_catalog(catalog_name=full_name or name))
        elif action == "list": return {"items": _to_dict_list(_list_catalogs())}
        elif action == "update":
            return _to_dict(_update_catalog(catalog_name=full_name or name, new_name=new_name,
                                            comment=comment, owner=owner, isolation_mode=isolation_mode))
        elif action == "delete":
            _delete_catalog(catalog_name=full_name or name, force=force)
            _remove_resource("catalog", full_name or name)
            return {"status": "deleted", "catalog": full_name or name}

    elif otype == "schema":
        if action == "create":
            result = _to_dict(_create_schema(catalog_name=catalog_name, schema_name=name, comment=comment))
            _auto_tag("schema", f"{catalog_name}.{name}")
            _track_resource("schema", f"{catalog_name}.{name}", result.get("full_name", f"{catalog_name}.{name}"))
            return result
        elif action == "get": return _to_dict(_get_schema(full_schema_name=full_name))
        elif action == "list": return {"items": _to_dict_list(_list_schemas(catalog_name=catalog_name))}
        elif action == "update":
            return _to_dict(_update_schema(full_schema_name=full_name, new_name=new_name,
                                           comment=comment, owner=owner))
        elif action == "delete":
            _delete_schema(full_schema_name=full_name)
            _remove_resource("schema", full_name)
            return {"status": "deleted", "schema": full_name}

    elif otype == "volume":
        if action == "create":
            result = _to_dict(_create_volume(catalog_name=catalog_name, schema_name=schema_name,
                                              name=name, volume_type=volume_type or "MANAGED",
                                              comment=comment, storage_location=storage_location))
            _auto_tag("volume", f"{catalog_name}.{schema_name}.{name}")
            _track_resource("volume", f"{catalog_name}.{schema_name}.{name}",
                            result.get("full_name", f"{catalog_name}.{schema_name}.{name}"))
            return result
        elif action == "get": return _to_dict(_get_volume(full_volume_name=full_name))
        elif action == "list": return {"items": _to_dict_list(_list_volumes(catalog_name=catalog_name, schema_name=schema_name))}
        elif action == "update":
            return _to_dict(_update_volume(full_volume_name=full_name, new_name=new_name,
                                           comment=comment, owner=owner))
        elif action == "delete":
            _delete_volume(full_volume_name=full_name)
            _remove_resource("volume", full_name)
            return {"status": "deleted", "volume": full_name}

    elif otype == "function":
        if action == "create": return {"error": "Use execute_sql with CREATE FUNCTION statement."}
        elif action == "get": return _to_dict(_get_function(full_function_name=full_name))
        elif action == "list": return {"items": _to_dict_list(_list_functions(catalog_name=catalog_name, schema_name=schema_name))}
        elif action == "delete":
            _delete_function(full_function_name=full_name, force=force)
            return {"status": "deleted", "function": full_name}

    return {"error": f"Invalid object_type='{object_type}' or action='{action}'"}


@mcp.tool(timeout=60)
def manage_uc_grants(
    action: str, securable_type: str, full_name: str,
    principal: str = None, privileges: List[str] = None,
) -> Dict:
    """Manage UC permissions: grant/revoke/get/get_effective.

    securable_type: catalog/schema/table/volume/function/storage_credential/external_location/connection/share.
    privileges: USE_CATALOG, SELECT, MODIFY, READ_VOLUME, EXECUTE, ALL_PRIVILEGES, etc."""
    act = action.lower()
    if act == "grant":
        return _grant_privileges(securable_type=securable_type, full_name=full_name,
                                  principal=principal, privileges=privileges)
    elif act == "revoke":
        return _revoke_privileges(securable_type=securable_type, full_name=full_name,
                                   principal=principal, privileges=privileges)
    elif act == "get":
        return {"privilege_assignments": _to_dict_list(_get_grants(securable_type=securable_type, full_name=full_name))}
    elif act == "get_effective":
        return {"privilege_assignments": _to_dict_list(_get_effective_grants(securable_type=securable_type, full_name=full_name, principal=principal))}
    return {"error": f"Invalid action '{action}'. Valid: grant, revoke, get, get_effective"}


@mcp.tool(timeout=60)
def manage_uc_storage(
    resource_type: str, action: str,
    name: str = None, new_name: str = None,
    url: str = None, credential_name: str = None,
    aws_iam_role_arn: str = None, azure_access_connector_id: str = None,
    comment: str = None, owner: str = None,
    read_only: bool = False, force: bool = False,
) -> Dict:
    """Manage storage credentials and external locations.

    resource_type: "credential" or "external_location".
    credential actions: create, get, update, validate, list, delete.
    external_location actions: create, get, update, list, delete."""
    rtype = resource_type.lower()
    act = action.lower()

    if rtype == "credential":
        if act == "create":
            return _to_dict(_create_storage_credential(name=name, aws_iam_role_arn=aws_iam_role_arn,
                                                        azure_access_connector_id=azure_access_connector_id,
                                                        comment=comment, read_only=read_only))
        elif act == "get": return _to_dict(_get_storage_credential(name=name))
        elif act == "list": return {"items": _to_dict_list(_list_storage_credentials())}
        elif act == "update":
            return _to_dict(_update_storage_credential(name=name, new_name=new_name,
                                                        comment=comment, owner=owner,
                                                        aws_iam_role_arn=aws_iam_role_arn,
                                                        azure_access_connector_id=azure_access_connector_id))
        elif act == "validate": return _validate_storage_credential(name=name, url=url)
        elif act == "delete":
            _delete_storage_credential(name=name, force=force)
            return {"status": "deleted", "credential": name}

    elif rtype == "external_location":
        if act == "create":
            return _to_dict(_create_external_location(name=name, url=url,
                                                       credential_name=credential_name,
                                                       comment=comment, read_only=read_only))
        elif act == "get": return _to_dict(_get_external_location(name=name))
        elif act == "list": return {"items": _to_dict_list(_list_external_locations())}
        elif act == "update":
            return _to_dict(_update_external_location(name=name, new_name=new_name,
                                                       url=url, credential_name=credential_name,
                                                       comment=comment, owner=owner, read_only=read_only))
        elif act == "delete":
            _delete_external_location(name=name, force=force)
            return {"status": "deleted", "external_location": name}

    return {"error": f"Invalid resource_type='{resource_type}' or action='{action}'"}


@mcp.tool(timeout=60)
def manage_uc_connections(
    action: str, name: str = None, new_name: str = None,
    connection_type: str = None, options: Dict[str, str] = None,
    owner: str = None, comment: str = None,
    catalog_name: str = None, connection_name: str = None,
    catalog_options: Dict[str, str] = None,
    warehouse_id: str = None,
) -> Dict:
    """Manage Lakehouse Federation foreign connections.

    connection_type: SNOWFLAKE, POSTGRESQL, MYSQL, SQLSERVER, BIGQUERY, REDSHIFT, SQLDW.
    actions: create, get, list, update, delete, create_foreign_catalog."""
    act = action.lower()
    if act == "create":
        return _to_dict(_create_connection(name=name, connection_type=connection_type,
                                            options=options, comment=comment))
    elif act == "get": return _to_dict(_get_connection(name=name))
    elif act == "list": return {"items": _to_dict_list(_list_connections())}
    elif act == "update":
        return _to_dict(_update_connection(name=name, options=options,
                                           new_name=new_name, owner=owner))
    elif act == "delete":
        _delete_connection(name=name)
        return {"status": "deleted", "connection": name}
    elif act == "create_foreign_catalog":
        return _to_dict(_create_foreign_catalog(catalog_name=catalog_name,
                                                  connection_name=connection_name,
                                                  catalog_options=catalog_options,
                                                  comment=comment))
    return {"error": f"Invalid action '{action}'"}


@mcp.tool(timeout=60)
def manage_uc_tags(
    action: str, object_type: str = None, full_name: str = None,
    tags: Dict[str, str] = None, tag_names: List[str] = None,
    column_name: str = None, comment_text: str = None,
    catalog_filter: str = None, table_name_filter: str = None,
    tag_name_filter: str = None, tag_value_filter: str = None,
    limit: int = 100, warehouse_id: str = None,
) -> Dict:
    """Manage UC tags and comments.

    actions: set_tags, unset_tags, set_comment, query_table_tags, query_column_tags."""
    act = action.lower()
    if act == "set_tags":
        _set_tags(object_type=object_type, full_name=full_name, tags=tags,
                  column_name=column_name, warehouse_id=warehouse_id)
        return {"status": "tags_set"}
    elif act == "unset_tags":
        _unset_tags(object_type=object_type, full_name=full_name, tag_names=tag_names,
                    column_name=column_name, warehouse_id=warehouse_id)
        return {"status": "tags_unset"}
    elif act == "set_comment":
        _set_comment(object_type=object_type, full_name=full_name, comment_text=comment_text,
                     column_name=column_name, warehouse_id=warehouse_id)
        return {"status": "comment_set"}
    elif act == "query_table_tags":
        return {"data": _query_table_tags(catalog_filter=catalog_filter, tag_name_filter=tag_name_filter,
                                           tag_value_filter=tag_value_filter, limit=limit)}
    elif act == "query_column_tags":
        return {"data": _query_column_tags(catalog_filter=catalog_filter, table_name_filter=table_name_filter,
                                            tag_name_filter=tag_name_filter, tag_value_filter=tag_value_filter,
                                            limit=limit)}
    return {"error": f"Invalid action '{action}'"}


@mcp.tool(timeout=60)
def manage_uc_security_policies(
    action: str, table_name: str = None, column_name: str = None,
    filter_function: str = None, filter_columns: List[str] = None,
    mask_function: str = None, function_name: str = None,
    parameter_name: str = None, parameter_type: str = None,
    return_type: str = None, function_body: str = None,
    function_comment: str = None, warehouse_id: str = None,
) -> Dict:
    """Manage row-level security and column masking.

    actions: set_row_filter, drop_row_filter, set_column_mask, drop_column_mask, create_security_function."""
    act = action.lower()
    if act == "set_row_filter":
        _set_row_filter(table_name=table_name, filter_function=filter_function,
                        filter_columns=filter_columns, warehouse_id=warehouse_id)
        return {"status": "row_filter_set"}
    elif act == "drop_row_filter":
        _drop_row_filter(table_name=table_name, warehouse_id=warehouse_id)
        return {"status": "row_filter_dropped"}
    elif act == "set_column_mask":
        _set_column_mask(table_name=table_name, column_name=column_name,
                         mask_function=mask_function, warehouse_id=warehouse_id)
        return {"status": "column_mask_set"}
    elif act == "drop_column_mask":
        _drop_column_mask(table_name=table_name, column_name=column_name, warehouse_id=warehouse_id)
        return {"status": "column_mask_dropped"}
    elif act == "create_security_function":
        return _create_security_function(function_name=function_name, parameter_name=parameter_name,
                                          parameter_type=parameter_type, return_type=return_type,
                                          function_body=function_body, comment=function_comment,
                                          warehouse_id=warehouse_id)
    return {"error": f"Invalid action '{action}'"}


@mcp.tool(timeout=60)
def manage_uc_monitors(
    action: str, table_name: str,
    output_schema_name: str = None, assets_dir: str = None,
    schedule_cron: str = None, schedule_timezone: str = "UTC",
) -> Dict:
    """Manage Lakehouse quality monitors: create, get, run_refresh, list_refreshes, delete."""
    act = action.lower()
    if act == "create":
        return _to_dict(_create_monitor(table_name=table_name,
                                         output_schema_name=output_schema_name,
                                         assets_dir=assets_dir, schedule_cron=schedule_cron,
                                         schedule_timezone=schedule_timezone))
    elif act == "get": return _to_dict(_get_monitor(table_name=table_name))
    elif act == "run_refresh":
        _run_monitor_refresh(table_name=table_name)
        return {"status": "refresh_triggered"}
    elif act == "list_refreshes":
        return {"refreshes": _to_dict_list(_list_monitor_refreshes(table_name=table_name))}
    elif act == "delete":
        _delete_monitor(table_name=table_name)
        return {"status": "deleted"}
    return {"error": f"Invalid action '{action}'"}


@mcp.tool(timeout=60)
def manage_uc_sharing(
    resource_type: str, action: str,
    name: str = None, share_name: str = None,
    table_name: str = None, shared_as: str = None,
    partition_spec: str = None, recipient_name: str = None,
    authentication_type: str = None, sharing_id: str = None,
    comment: str = None, ip_access_list: List[str] = None,
    include_shared_data: bool = True,
) -> Dict:
    """Manage Delta Sharing: shares, recipients, and providers.

    resource_type: "share", "recipient", or "provider".
    Share actions: create, get, list, delete, add_table, remove_table, grant_to_recipient, revoke_from_recipient.
    Recipient actions: create, get, list, delete, rotate_token.
    Provider actions: get, list, list_shares."""
    rtype = resource_type.lower()
    act = action.lower()
    sn = share_name or name

    if rtype == "share":
        if act == "create":
            result = _to_dict(_create_share(name=name, comment=comment))
            _track_resource("share", name, name)
            return result
        elif act == "get": return _to_dict(_get_share(share_name=sn, include_shared_data=include_shared_data))
        elif act == "list": return {"items": _to_dict_list(_list_shares())}
        elif act == "delete":
            _delete_share(share_name=sn)
            return {"status": "deleted", "share": sn}
        elif act == "add_table":
            return _to_dict(_add_table_to_share(share_name=sn, table_name=table_name,
                                                 shared_as=shared_as, partition_spec=partition_spec))
        elif act == "remove_table":
            _remove_table_from_share(share_name=sn, table_name=table_name)
            return {"status": "table_removed"}
        elif act == "grant_to_recipient":
            _grant_share_to_recipient(share_name=sn, recipient_name=recipient_name)
            return {"status": "granted"}
        elif act == "revoke_from_recipient":
            _revoke_share_from_recipient(share_name=sn, recipient_name=recipient_name)
            return {"status": "revoked"}

    elif rtype == "recipient":
        if act == "create":
            return _to_dict(_create_recipient(name=name, authentication_type=authentication_type,
                                               sharing_id=sharing_id, comment=comment,
                                               ip_access_list=ip_access_list))
        elif act == "get": return _to_dict(_get_recipient(name=name))
        elif act == "list": return {"items": _to_dict_list(_list_recipients())}
        elif act == "delete":
            _delete_recipient(name=name)
            return {"status": "deleted", "recipient": name}
        elif act == "rotate_token": return _to_dict(_rotate_recipient_token(name=name))

    elif rtype == "provider":
        if act == "get": return _to_dict(_get_provider(name=name))
        elif act == "list": return {"items": _to_dict_list(_list_providers())}
        elif act == "list_shares": return {"items": _to_dict_list(_list_provider_shares(provider_name=name))}

    return {"error": f"Invalid resource_type='{resource_type}' or action='{action}'"}


@mcp.tool(timeout=60)
def manage_metric_views(
    action: str, full_name: str,
    source: str = None, dimensions: List[Dict[str, str]] = None,
    measures: List[Dict[str, str]] = None, version: str = "1.1",
    comment: str = None, filter_expr: str = None,
    joins: List[Dict] = None, materialization: Dict = None,
    or_replace: bool = False, query_measures: List[str] = None,
    query_dimensions: List[str] = None, where: str = None,
    order_by: str = None, limit: int = None,
    principal: str = None, privileges: List[str] = None,
    warehouse_id: str = None,
) -> Dict:
    """Manage UC metric views (reusable business metrics). Requires DBR 17.2+.

    actions: create, alter, describe, query, drop, grant."""
    act = action.lower()
    if act == "create":
        return _to_dict(_create_metric_view(full_name=full_name, source=source,
                                             dimensions=dimensions, measures=measures,
                                             version=version, comment=comment,
                                             filter_expr=filter_expr, joins=joins,
                                             materialization=materialization, or_replace=or_replace,
                                             warehouse_id=warehouse_id))
    elif act == "alter":
        return _to_dict(_alter_metric_view(full_name=full_name, source=source,
                                            dimensions=dimensions, measures=measures,
                                            version=version, comment=comment,
                                            filter_expr=filter_expr, joins=joins,
                                            materialization=materialization,
                                            warehouse_id=warehouse_id))
    elif act == "describe": return _to_dict(_describe_metric_view(full_name=full_name, warehouse_id=warehouse_id))
    elif act == "query":
        return {"data": _query_metric_view(full_name=full_name, query_measures=query_measures,
                                            query_dimensions=query_dimensions, where=where,
                                            order_by=order_by, limit=limit, warehouse_id=warehouse_id)}
    elif act == "drop":
        _drop_metric_view(full_name=full_name, warehouse_id=warehouse_id)
        return {"status": "dropped"}
    elif act == "grant":
        _grant_metric_view(full_name=full_name, principal=principal,
                            privileges=privileges, warehouse_id=warehouse_id)
        return {"status": "granted"}
    return {"error": f"Invalid action '{action}'"}


# ===========================================================================
# ── JOBS TOOLS ──────────────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool(timeout=60)
def manage_jobs(
    action: str, job_id: int = None, name: str = None,
    tasks: List[Dict] = None, job_clusters: List[Dict] = None,
    environments: List[Dict] = None, tags: Dict[str, str] = None,
    timeout_seconds: int = None, max_concurrent_runs: int = None,
    email_notifications: Dict = None, webhook_notifications: Dict = None,
    notification_settings: Dict = None, schedule: Dict = None,
    queue: Dict = None, run_as: Dict = None, git_source: Dict = None,
    parameters: List[Dict] = None, health: Dict = None,
    deployment: Dict = None, limit: int = 25, expand_tasks: bool = False,
) -> Dict:
    """Manage Databricks jobs: create, get, list, find_by_name, update, delete.

    create: requires name+tasks, idempotent (returns existing if same name).
    tasks: [{task_key, notebook_task|spark_python_task|..., job_cluster_key or environment_key}].
    Returns: create={job_id}, get=full config, list={items}, find_by_name={job_id}, update/delete={status, job_id}."""
    act = action.lower()
    if act == "create":
        existing = _find_job_by_name(name=name)
        if existing is not None:
            return {"job_id": existing, "already_exists": True,
                    "message": f"Job '{name}' already exists (job_id={existing})."}
        merged_tags = {**get_default_tags(), **(tags or {})}
        result = _create_job(name=name, tasks=tasks, job_clusters=job_clusters,
                              environments=environments, tags=merged_tags,
                              timeout_seconds=timeout_seconds,
                              max_concurrent_runs=max_concurrent_runs or 1,
                              email_notifications=email_notifications,
                              webhook_notifications=webhook_notifications,
                              notification_settings=notification_settings,
                              schedule=schedule, queue=queue, run_as=run_as,
                              git_source=git_source, parameters=parameters,
                              health=health, deployment=deployment)
        if isinstance(result, dict) and result.get("job_id"):
            _track_resource("job", name, str(result["job_id"]))
        return result
    elif act == "get": return _get_job(job_id=job_id)
    elif act == "list": return {"items": _list_jobs(name=name, limit=limit, expand_tasks=expand_tasks)}
    elif act == "find_by_name": return {"job_id": _find_job_by_name(name=name)}
    elif act == "update":
        _update_job(job_id=job_id, name=name, tasks=tasks, job_clusters=job_clusters,
                    environments=environments, tags=tags, timeout_seconds=timeout_seconds,
                    max_concurrent_runs=max_concurrent_runs, email_notifications=email_notifications,
                    webhook_notifications=webhook_notifications, notification_settings=notification_settings,
                    schedule=schedule, queue=queue, run_as=run_as, git_source=git_source,
                    parameters=parameters, health=health, deployment=deployment)
        return {"status": "updated", "job_id": job_id}
    elif act == "delete":
        _delete_job(job_id=job_id)
        _remove_resource("job", str(job_id))
        return {"status": "deleted", "job_id": job_id}
    return {"error": f"Invalid action '{action}'"}


@mcp.tool(timeout=300)
def manage_job_runs(
    action: str, job_id: int = None, run_id: int = None,
    idempotency_token: str = None, jar_params: List[str] = None,
    notebook_params: Dict[str, str] = None, python_params: List[str] = None,
    spark_submit_params: List[str] = None, python_named_params: Dict[str, str] = None,
    pipeline_params: Dict = None, sql_params: Dict[str, str] = None,
    dbt_commands: List[str] = None, queue: Dict = None,
    rerun_all_failed_tasks: bool = None, rerun_dependent_tasks: bool = None,
    rerun_tasks: List[str] = None, latest_repair_id: int = None,
    active_only: bool = False, completed_only: bool = False,
    limit: int = 25, offset: int = 0,
    start_time_from: int = None, start_time_to: int = None,
    timeout: int = 3600, poll_interval: int = 10,
) -> Dict:
    """Manage job runs: run_now, repair, get, get_output, cancel, list, wait."""
    act = action.lower()
    if act == "run_now":
        return {"run_id": _run_job_now(job_id=job_id, idempotency_token=idempotency_token,
                                        jar_params=jar_params, notebook_params=notebook_params,
                                        python_params=python_params, spark_submit_params=spark_submit_params,
                                        python_named_params=python_named_params,
                                        pipeline_params=pipeline_params, sql_params=sql_params,
                                        dbt_commands=dbt_commands, queue=queue)}
    elif act == "repair":
        return {"repair_id": _repair_run(run_id=run_id, rerun_all_failed_tasks=rerun_all_failed_tasks,
                                          rerun_dependent_tasks=rerun_dependent_tasks,
                                          rerun_tasks=rerun_tasks, latest_repair_id=latest_repair_id),
                "run_id": run_id}
    elif act == "get": return _get_run(run_id=run_id)
    elif act == "get_output": return _get_run_output(run_id=run_id)
    elif act == "cancel":
        _cancel_run(run_id=run_id)
        return {"status": "cancelled", "run_id": run_id}
    elif act == "list":
        return {"items": _list_runs(job_id=job_id, active_only=active_only,
                                     completed_only=completed_only, limit=limit, offset=offset,
                                     start_time_from=start_time_from, start_time_to=start_time_to)}
    elif act == "wait":
        result = _wait_for_run(run_id=run_id, timeout=timeout, poll_interval=poll_interval)
        return result.to_dict()
    return {"error": f"Invalid action '{action}'"}


# ===========================================================================
# ── PIPELINE TOOLS ──────────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool(timeout=300)
def manage_pipeline(
    action: str, name: str = None, pipeline_id: str = None,
    root_path: str = None, catalog: str = None, schema: str = None,
    workspace_file_paths: List[str] = None, extra_settings: Dict = None,
    start_run: bool = False, wait_for_completion: bool = False,
    full_refresh: bool = True, timeout: int = 1800,
) -> Dict:
    """Manage Spark Declarative Pipelines: create, create_or_update, get, update, delete, find_by_name.

    root_path: Workspace folder for pipeline files. workspace_file_paths: Notebooks/files in pipeline.
    extra_settings: Additional config (clusters, photon, channel, continuous, etc)."""
    act = action.lower()
    if act in ("create", "create_or_update"):
        if not all([name, root_path, catalog, schema, workspace_file_paths]):
            return {"error": f"{act} requires: name, root_path, catalog, schema, workspace_file_paths"}
        settings = extra_settings or {}
        settings.setdefault("tags", {})
        settings["tags"] = {**get_default_tags(), **settings["tags"]}
        if act == "create":
            result = _create_pipeline(name=name, root_path=root_path, catalog=catalog,
                                       schema=schema, workspace_file_paths=workspace_file_paths,
                                       extra_settings=settings)
            _track_resource("pipeline", name, result.pipeline_id)
            return {"pipeline_id": result.pipeline_id}
        else:
            result = _create_or_update_pipeline(
                name=name, root_path=root_path, catalog=catalog, schema=schema,
                workspace_file_paths=workspace_file_paths, start_run=start_run,
                wait_for_completion=wait_for_completion, full_refresh=full_refresh,
                timeout=timeout, extra_settings=settings)
            r = result.to_dict()
            if r.get("pipeline_id"):
                _track_resource("pipeline", name, r["pipeline_id"])
            return r
    elif act == "get":
        if not pipeline_id: return {"error": "get requires: pipeline_id"}
        result = _get_pipeline(pipeline_id=pipeline_id)
        return result.as_dict() if hasattr(result, "as_dict") else vars(result)
    elif act == "update":
        if not pipeline_id: return {"error": "update requires: pipeline_id"}
        _update_pipeline(pipeline_id=pipeline_id, name=name, root_path=root_path,
                          catalog=catalog, schema=schema, workspace_file_paths=workspace_file_paths,
                          extra_settings=extra_settings)
        return {"status": "updated", "pipeline_id": pipeline_id}
    elif act == "delete":
        if not pipeline_id: return {"error": "delete requires: pipeline_id"}
        _delete_pipeline(pipeline_id=pipeline_id)
        _remove_resource("pipeline", pipeline_id)
        return {"status": "deleted", "pipeline_id": pipeline_id}
    elif act == "find_by_name":
        if not name: return {"error": "find_by_name requires: name"}
        pid = _find_pipeline_by_name(name=name)
        return {"found": pid is not None, "pipeline_id": pid, "name": name}
    return {"error": f"Invalid action '{action}'"}


@mcp.tool(timeout=300)
def manage_pipeline_run(
    action: str, pipeline_id: str,
    refresh_selection: List[str] = None, full_refresh: bool = False,
    full_refresh_selection: List[str] = None, validate_only: bool = False,
    wait: bool = True, timeout: int = 300,
    update_id: str = None, include_config: bool = False,
    full_error_details: bool = False, max_results: int = 5,
    event_log_level: str = "WARN",
) -> Dict:
    """Manage pipeline runs: start, get, stop, get_events."""
    act = action.lower()
    if act == "start":
        return _start_update(pipeline_id=pipeline_id, refresh_selection=refresh_selection,
                              full_refresh=full_refresh, full_refresh_selection=full_refresh_selection,
                              validate_only=validate_only, wait=wait, timeout=timeout,
                              full_error_details=full_error_details)
    elif act == "get":
        if not update_id: return {"error": "get requires: update_id"}
        return _get_update(pipeline_id=pipeline_id, update_id=update_id,
                            include_config=include_config, full_error_details=full_error_details)
    elif act == "stop":
        _stop_pipeline(pipeline_id=pipeline_id)
        return {"status": "stopped", "pipeline_id": pipeline_id}
    elif act == "get_events":
        level_map = {"ERROR": "level='ERROR'", "WARN": "level in ('ERROR', 'WARN')", "INFO": ""}
        events = _get_pipeline_events(pipeline_id=pipeline_id, max_results=max_results,
                                       filter=level_map.get(event_log_level.upper(), ""),
                                       update_id=update_id)
        return {"events": [e.as_dict() if hasattr(e, "as_dict") else vars(e) for e in events]}
    return {"error": f"Invalid action '{action}'"}


# ===========================================================================
# ── VECTOR SEARCH TOOLS ─────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool(timeout=120)
def manage_vs_endpoint(
    action: str, name: str = None, endpoint_type: str = "STANDARD",
) -> Dict:
    """Manage Vector Search endpoints: create_or_update, get, list, delete.

    endpoint_type: STANDARD (<100ms) or STORAGE_OPTIMIZED (~250ms, 1B+ vectors).
    Returns: {name, endpoint_type, state, created: bool}."""
    act = action.lower()
    if act == "create_or_update":
        if not name: return {"error": "requires: name"}
        try:
            existing = _get_vs_endpoint(name=name)
            if existing.get("state") != "NOT_FOUND":
                return {**existing, "created": False}
        except Exception:
            pass
        result = _create_vs_endpoint(name=name, endpoint_type=endpoint_type)
        _track_resource("vs_endpoint", name, name)
        return {**result, "created": True}
    elif act == "get":
        if not name: return {"error": "requires: name"}
        return _get_vs_endpoint(name=name)
    elif act == "list": return {"endpoints": _list_vs_endpoints()}
    elif act == "delete":
        if not name: return {"error": "requires: name"}
        return _delete_vs_endpoint(name=name)
    return {"error": f"Invalid action '{action}'"}


@mcp.tool(timeout=120)
def manage_vs_index(
    action: str, name: str = None, endpoint_name: str = None,
    primary_key: str = None, index_type: str = "DELTA_SYNC",
    delta_sync_index_spec: Dict = None, direct_access_index_spec: Dict = None,
) -> Dict:
    """Manage Vector Search indexes: create_or_update, get, list, delete.

    index_type: DELTA_SYNC (auto-sync) or DIRECT_ACCESS (manual CRUD).
    delta_sync_index_spec: {source_table, embedding_source_columns OR embedding_vector_columns, pipeline_type}."""
    act = action.lower()
    if act == "create_or_update":
        if not all([name, endpoint_name, primary_key]):
            return {"error": "requires: name, endpoint_name, primary_key"}
        try:
            existing = _get_vs_index(index_name=name)
            if existing.get("state") != "NOT_FOUND":
                return {**existing, "created": False}
        except Exception:
            pass
        result = _create_vs_index(name=name, endpoint_name=endpoint_name, primary_key=primary_key,
                                    index_type=index_type, delta_sync_index_spec=delta_sync_index_spec,
                                    direct_access_index_spec=direct_access_index_spec)
        if index_type == "DELTA_SYNC":
            try:
                _sync_vs_index(index_name=name)
                result["sync_triggered"] = True
            except Exception:
                result["sync_triggered"] = False
        _track_resource("vs_index", name, name)
        return {**result, "created": True}
    elif act == "get":
        if not name: return {"error": "requires: name"}
        return _get_vs_index(index_name=name)
    elif act == "list":
        if endpoint_name:
            return {"indexes": _list_vs_indexes(endpoint_name=endpoint_name)}
        all_indexes = []
        for ep in _list_vs_endpoints():
            ep_name = ep.get("name")
            if ep_name:
                try:
                    for idx in _list_vs_indexes(endpoint_name=ep_name):
                        idx["endpoint_name"] = ep_name
                        all_indexes.append(idx)
                except Exception:
                    pass
        return {"indexes": all_indexes}
    elif act == "delete":
        if not name: return {"error": "requires: name"}
        return _delete_vs_index(index_name=name)
    return {"error": f"Invalid action '{action}'"}


@mcp.tool(timeout=60)
def query_vs_index(
    index_name: str, columns: List[str],
    query_text: str = None, query_vector: List[float] = None,
    num_results: int = 5, filters_json: Union[str, dict] = None,
    filter_string: str = None, query_type: str = None,
) -> Dict:
    """Query a Vector Search index for similar documents.

    Use query_text (managed embeddings) OR query_vector (self-managed).
    filters_json: For STANDARD endpoints. filter_string: For STORAGE_OPTIMIZED (SQL WHERE).
    query_type: ANN (default) or HYBRID."""
    if isinstance(filters_json, dict):
        filters_json = json.dumps(filters_json)
    return _query_vs_index(index_name=index_name, columns=columns, query_text=query_text,
                            query_vector=query_vector, num_results=num_results,
                            filters_json=filters_json, filter_string=filter_string,
                            query_type=query_type)


@mcp.tool(timeout=120)
def manage_vs_data(
    action: str, index_name: str,
    inputs_json: Union[str, list] = None,
    primary_keys: List[str] = None, num_results: int = 100,
) -> Dict:
    """Manage Vector Search index data: upsert, delete, scan, sync."""
    act = action.lower()
    if act == "upsert":
        if inputs_json is None: return {"error": "upsert requires: inputs_json"}
        if isinstance(inputs_json, (dict, list)): inputs_json = json.dumps(inputs_json)
        return _upsert_vs_data(index_name=index_name, inputs_json=inputs_json)
    elif act == "delete":
        if primary_keys is None: return {"error": "delete requires: primary_keys"}
        return _delete_vs_data(index_name=index_name, primary_keys=primary_keys)
    elif act == "scan": return _scan_vs_index(index_name=index_name, num_results=num_results)
    elif act == "sync":
        _sync_vs_index(index_name=index_name)
        return {"index_name": index_name, "status": "sync_triggered"}
    return {"error": f"Invalid action '{action}'"}


# ===========================================================================
# ── SERVING TOOL ────────────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool(timeout=120)
def manage_serving_endpoint(
    action: str, name: str = None,
    messages: List[Dict[str, str]] = None,
    inputs: Dict = None,
    dataframe_records: List[Dict] = None,
    max_tokens: int = None, temperature: float = None,
    limit: int = 50,
) -> Dict:
    """Manage Model Serving endpoints: get, list, query.

    query input formats (use one): messages (chat), inputs (pyfunc), dataframe_records (ML).
    Returns: {choices: [...]} for chat or {predictions: [...]} for ML."""
    act = action.lower()
    if act == "get":
        if not name: return {"error": "requires: name"}
        return _get_serving_endpoint_status(name=name)
    elif act == "list":
        return {"endpoints": _list_serving_endpoints(limit=limit)}
    elif act == "query":
        if not name: return {"error": "requires: name"}
        if not any([messages, inputs, dataframe_records]):
            return {"error": "query requires one of: messages, inputs, dataframe_records"}
        return _query_serving_endpoint(name=name, messages=messages, inputs=inputs,
                                        dataframe_records=dataframe_records,
                                        max_tokens=max_tokens, temperature=temperature)
    return {"error": f"Invalid action '{action}'"}


# ===========================================================================
# ── GENIE TOOLS ─────────────────────────────────────────────────────────────
# ===========================================================================

_genie_manager: Optional[AgentBricksManager] = None


def _get_genie_manager() -> AgentBricksManager:
    global _genie_manager
    if _genie_manager is None:
        _genie_manager = AgentBricksManager()
    return _genie_manager


@mcp.tool(timeout=120)
def manage_genie(
    action: str, space_id: str = None, display_name: str = None,
    description: str = None, table_identifiers: List[str] = None,
    warehouse_id: str = None, sample_questions: List[str] = None,
    serialized_space: str = None, include_serialized_space: bool = False,
    title: str = None, parent_path: str = None,
) -> Dict:
    """Manage Genie Spaces: create_or_update, get, list, delete, export, import.

    create_or_update: Idempotent by display_name. Requires display_name, table_identifiers.
    serialized_space: Full config from export (preserves instructions/SQL examples)."""
    act = action.lower()
    manager = _get_genie_manager()
    w = get_workspace_client()

    if act == "create_or_update":
        if not display_name or not table_identifiers:
            return {"error": "create_or_update requires: display_name, table_identifiers"}
        if not warehouse_id:
            from databricks_tools_core.sql import get_best_warehouse as _gbw
            warehouse_id = _gbw()
        result = manager.genie_create_or_update(
            display_name=display_name, description=with_description_footer(description),
            table_identifiers=table_identifiers, warehouse_id=warehouse_id,
            sample_questions=sample_questions, serialized_space=serialized_space)
        space_id_val = result.get("space_id", "")
        if space_id_val:
            _track_resource("genie_space", display_name, space_id_val)
        return result

    elif act == "get":
        if not space_id: return {"error": "requires: space_id"}
        return manager.genie_get(space_id=space_id, include_serialized_space=include_serialized_space)

    elif act == "list":
        spaces = list(w.genie.list_spaces())
        return {"spaces": [{"space_id": s.space_id, "title": s.title, "description": s.description}
                            for s in spaces]}

    elif act == "delete":
        if not space_id: return {"error": "requires: space_id"}
        w.genie.trash_space(space_id=space_id)
        _remove_resource("genie_space", space_id)
        return {"success": True, "space_id": space_id}

    elif act == "export":
        if not space_id: return {"error": "requires: space_id"}
        return manager.genie_export(space_id=space_id)

    elif act == "import":
        if not warehouse_id or not serialized_space:
            return {"error": "import requires: warehouse_id, serialized_space"}
        return manager.genie_import(warehouse_id=warehouse_id, serialized_space=serialized_space,
                                     title=title, description=description, parent_path=parent_path)

    return {"error": f"Invalid action '{action}'"}


@mcp.tool(timeout=120)
def ask_genie(
    space_id: str, question: str,
    conversation_id: str = None, timeout_seconds: int = 120,
) -> Dict:
    """Ask a natural language question to a Genie Space. Pass conversation_id for follow-ups."""
    manager = _get_genie_manager()
    return manager.genie_ask(space_id=space_id, question=question,
                              conversation_id=conversation_id, timeout_seconds=timeout_seconds)


# ===========================================================================
# ── AGENT BRICKS TOOLS ──────────────────────────────────────────────────────
# ===========================================================================

_ab_manager: Optional[AgentBricksManager] = None


def _get_ab_manager() -> AgentBricksManager:
    global _ab_manager
    if _ab_manager is None:
        _ab_manager = AgentBricksManager()
    return _ab_manager


@mcp.tool(timeout=180)
def manage_ka(
    action: str, name: str = None, volume_path: str = None,
    description: str = None, instructions: str = None,
    tile_id: str = None, add_examples_from_volume: bool = True,
) -> Dict:
    """Manage Knowledge Assistant (KA) - RAG-based document Q&A.

    Actions: create_or_update (name+volume_path), get (tile_id), find_by_name (name), delete (tile_id).
    volume_path: UC Volume path with documents. Returns: {tile_id, operation, endpoint_status}."""
    act = action.lower()
    manager = _get_ab_manager()

    if act == "create_or_update":
        if not name or not volume_path: return {"error": "requires: name, volume_path"}
        ks = [{"files_source": {"name": f"source_{name.replace(' ','_').lower()}",
                                 "type": "files", "files": {"path": volume_path}}}]
        result = manager.ka_create_or_update(name=name, knowledge_sources=ks,
                                              description=with_description_footer(description),
                                              instructions=instructions, tile_id=tile_id)
        tid = result.get("tile_id", "")
        if tid:
            _track_resource("knowledge_assistant", name, tid)
            if add_examples_from_volume:
                examples = manager.scan_volume_for_examples(volume_path)
                if examples:
                    state = result.get("state", "")
                    if state == "ACTIVE":
                        manager.ka_add_examples_batch(tid, examples)
                        result["examples_added"] = len(examples)
                    else:
                        get_tile_example_queue().enqueue(tid, manager, examples, tile_type="KA")
                        result["examples_queued"] = len(examples)
        return result

    elif act == "get":
        if not tile_id: return {"error": "requires: tile_id"}
        return manager.ka_get(tile_id)

    elif act == "find_by_name":
        if not name: return {"error": "requires: name"}
        result = manager.find_by_name(name)
        if result is None: return {"found": False, "name": name}
        return {"found": True, "tile_id": result.tile_id, "name": result.name}

    elif act == "delete":
        if not tile_id: return {"error": "requires: tile_id"}
        manager.delete(tile_id)
        _remove_resource("knowledge_assistant", tile_id)
        return {"success": True, "tile_id": tile_id}

    return {"error": f"Invalid action '{action}'"}


@mcp.tool(timeout=180)
def manage_mas(
    action: str, name: str = None,
    agents: List[Dict[str, str]] = None, description: str = None,
    instructions: str = None, tile_id: str = None,
    examples: List[Dict[str, str]] = None,
) -> Dict:
    """Manage Supervisor Agent (MAS) - orchestrates multiple agents.

    Actions: create_or_update (name+agents), get (tile_id), find_by_name (name), delete (tile_id).
    agents: [{name, description, ONE OF: endpoint_name|genie_space_id|ka_tile_id|uc_function_name|connection_name}]."""
    act = action.lower()
    manager = _get_ab_manager()

    if act == "create_or_update":
        if not name or not agents: return {"error": "requires: name, agents"}
        # Build agent list (simplified — delegates to manager)
        result = manager.mas_create_or_update(name=name, agents=agents,
                                               description=with_description_footer(description),
                                               instructions=instructions, tile_id=tile_id,
                                               examples=examples)
        tid = result.get("tile_id", "")
        if tid: _track_resource("multi_agent_supervisor", name, tid)
        return result

    elif act == "get":
        if not tile_id: return {"error": "requires: tile_id"}
        return manager.mas_get(tile_id)

    elif act == "find_by_name":
        if not name: return {"error": "requires: name"}
        result = manager.mas_find_by_name(name)
        if result is None: return {"found": False, "name": name}
        return {"found": True, "tile_id": result.tile_id, "name": result.name}

    elif act == "delete":
        if not tile_id: return {"error": "requires: tile_id"}
        manager.delete(tile_id)
        _remove_resource("multi_agent_supervisor", tile_id)
        return {"success": True, "tile_id": tile_id}

    return {"error": f"Invalid action '{action}'"}


# ===========================================================================
# ── LAKEBASE TOOLS ──────────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool(timeout=120)
def manage_lakebase_database(
    action: str, name: str = None, type: str = "provisioned",
    capacity: str = "CU_1", stopped: bool = False,
    display_name: str = None, pg_version: str = "17", force: bool = False,
) -> Dict:
    """Manage Lakebase PostgreSQL databases: create_or_update, get, list, delete.

    type: "provisioned" (fixed capacity CU_1/2/4/8) or "autoscale" (auto-scaling with branches)."""
    act = action.lower()
    dtype = type.lower()

    if act == "create_or_update":
        if not name: return {"error": "requires: name"}
        if dtype == "provisioned":
            try:
                result = _update_instance(name=name, capacity=capacity, stopped=stopped)
                return {**result, "created": False, "type": "provisioned"}
            except Exception:
                result = _create_instance(name=name, capacity=capacity, stopped=stopped)
                _track_resource("lakebase_instance", name, name)
                return {**result, "created": True, "type": "provisioned"}
        elif dtype == "autoscale":
            try:
                result = _update_project(name=name, display_name=display_name)
                return {**result, "created": False, "type": "autoscale"}
            except Exception:
                result = _create_project(project_id=name, display_name=display_name, pg_version=pg_version)
                _track_resource("lakebase_project", name, name)
                return {**result, "created": True, "type": "autoscale"}
        return {"error": f"Invalid type '{type}'"}

    elif act == "get":
        if not name: return {"error": "requires: name"}
        if dtype == "provisioned":
            return {**_get_instance(name=name), "type": "provisioned"}
        else:
            result = {**_get_project(name=name), "type": "autoscale"}
            try: result["branches"] = _list_branches(project_name=name)
            except Exception: pass
            return result

    elif act == "list":
        databases = []
        if dtype in ("provisioned", "provisioned"):
            try:
                for i in _list_instances(): databases.append({**i, "type": "provisioned"})
            except Exception: pass
        if dtype in ("autoscale", "provisioned"):
            try:
                for p in _list_projects(): databases.append({**p, "type": "autoscale"})
            except Exception: pass
        return {"databases": databases}

    elif act == "delete":
        if not name: return {"error": "requires: name"}
        if dtype == "provisioned": return _delete_instance(name=name, force=force, purge=True)
        elif dtype == "autoscale": return _delete_project(name=name)
        return {"error": f"Invalid type '{type}'"}

    return {"error": f"Invalid action '{action}'"}


@mcp.tool(timeout=120)
def manage_lakebase_branch(
    action: str, project_name: str = None, branch_id: str = None,
    source_branch: str = None, ttl_seconds: int = None,
    no_expiry: bool = False, is_protected: bool = None,
    endpoint_type: str = "ENDPOINT_TYPE_READ_WRITE",
    autoscaling_limit_min_cu: float = None, autoscaling_limit_max_cu: float = None,
    scale_to_zero_seconds: int = None, name: str = None,
) -> Dict:
    """Manage Autoscale branches: create_or_update, delete.

    Branches are isolated copy-on-write environments with their own compute endpoints."""
    act = action.lower()
    if act == "create_or_update":
        if not project_name or not branch_id: return {"error": "requires: project_name, branch_id"}
        result = _create_branch(project_name=project_name, branch_id=branch_id,
                                 source_branch=source_branch, ttl_seconds=ttl_seconds,
                                 no_expiry=no_expiry)
        branch_name = result.get("name", f"{project_name}/branches/{branch_id}")
        endpoint_result = None
        try:
            endpoint_result = _create_endpoint(branch_name=branch_name, endpoint_type=endpoint_type,
                                                autoscaling_limit_min_cu=autoscaling_limit_min_cu,
                                                autoscaling_limit_max_cu=autoscaling_limit_max_cu,
                                                scale_to_zero_seconds=scale_to_zero_seconds)
        except Exception as e:
            logger.warning("Failed to create endpoint: %s", e)
        final = {**result, "created": True}
        if endpoint_result: final["endpoint"] = endpoint_result
        return final
    elif act == "delete":
        if not name: return {"error": "requires: name (full branch name)"}
        return _delete_branch(name=name)
    return {"error": f"Invalid action '{action}'"}


@mcp.tool(timeout=120)
def manage_lakebase_sync(
    action: str, instance_name: str = None,
    source_table_name: str = None, target_table_name: str = None,
    catalog_name: str = None, database_name: str = "databricks_postgres",
    primary_key_columns: List[str] = None, scheduling_policy: str = "TRIGGERED",
    table_name: str = None,
) -> Dict:
    """Manage Lakebase sync (reverse ETL): create_or_update, delete.

    create_or_update: Set up reverse ETL from Delta table to Lakebase.
    source_table_name: Delta table (catalog.schema.table). scheduling_policy: TRIGGERED/SNAPSHOT/CONTINUOUS."""
    act = action.lower()
    if act == "create_or_update":
        if not all([instance_name, source_table_name, target_table_name]):
            return {"error": "requires: instance_name, source_table_name, target_table_name"}
        if not catalog_name:
            catalog_name = f"lakebase_{instance_name.replace('-', '_')}"
        try:
            _create_lakebase_catalog(instance_name=instance_name, catalog_name=catalog_name)
        except Exception:
            pass
        result = _create_synced_table(catalog_name=catalog_name, database_name=database_name,
                                       source_table_name=source_table_name,
                                       target_table_name=target_table_name,
                                       primary_key_columns=primary_key_columns,
                                       scheduling_policy=scheduling_policy)
        return {"catalog": catalog_name, "synced_table": result, "created": True}
    elif act == "delete":
        if not table_name: return {"error": "requires: table_name"}
        _delete_synced_table(table_name=table_name)
        result = {"synced_table": table_name}
        if catalog_name:
            try:
                from databricks_tools_core.lakebase import delete_lakebase_catalog as _del_cat
                _del_cat(catalog_name=catalog_name)
                result["catalog"] = catalog_name
            except Exception:
                pass
        return result
    return {"error": f"Invalid action '{action}'"}


@mcp.tool(timeout=30)
def generate_lakebase_credential(
    instance_names: List[str] = None, endpoint: str = None,
) -> Dict:
    """Generate OAuth token (~1hr) for Lakebase connection. Use as password with sslmode=require.

    Provide instance_names (provisioned) or endpoint (autoscale)."""
    if instance_names: return _generate_provisioned_credential(instance_names=instance_names)
    if endpoint: return _generate_autoscale_credential(endpoint=endpoint)
    return {"error": "Provide either instance_names (provisioned) or endpoint (autoscale)."}


# ===========================================================================
# ── APPS TOOL ───────────────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool(timeout=180)
def manage_app(
    action: str, name: str = None,
    description: str = None, source_code_path: str = None,
    mode: str = None, deployment_id: str = None,
    include_logs: bool = False, name_contains: str = None,
) -> Dict:
    """Manage Databricks Apps: create_or_update, get, list, delete.

    source_code_path: Volume or workspace path to deploy from.
    Returns: {name, created: bool, url, status, deployment}."""
    act = action.lower()
    if act == "create_or_update":
        if not name: return {"error": "requires: name"}
        result = _create_app(name=name, description=description)
        _track_resource("app", name, name)
        if source_code_path:
            deploy_result = _deploy_app(name=name, source_code_path=source_code_path, mode=mode)
            return {**result, "deployment": deploy_result, "created": True}
        return {**result, "created": True}
    elif act == "get":
        if not name: return {"error": "requires: name"}
        result = _get_app(name=name)
        if include_logs:
            try: result["logs"] = _get_app_logs(name=name)
            except Exception: pass
        return result
    elif act == "list":
        apps = _list_apps()
        if name_contains:
            apps = [a for a in apps if name_contains.lower() in a.get("name", "").lower()]
        return {"apps": apps}
    elif act == "delete":
        if not name: return {"error": "requires: name"}
        _delete_app(name=name)
        _remove_resource("app", name)
        return {"name": name, "status": "deleted"}
    return {"error": f"Invalid action '{action}'"}


# ===========================================================================
# ── DASHBOARD TOOL ──────────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool(timeout=120)
def manage_dashboard(
    action: str, dashboard_id: str = None,
    display_name: str = None, parent_path: str = None,
    serialized_dashboard: Union[str, Dict] = None,
    warehouse_id: str = None, publish: bool = True,
    embed_credentials: bool = True,
) -> Dict:
    """Manage AI/BI dashboards: create_or_update, get, list, delete, publish, unpublish.

    IMPORTANT: Test all dataset queries with execute_sql before creating dashboard.
    serialized_dashboard: Full dashboard JSON config.
    Returns: {success, dashboard_id, path, url, published, error}."""
    act = action.lower()
    if act == "create_or_update":
        if not all([display_name, parent_path, serialized_dashboard, warehouse_id]):
            return {"error": "requires: display_name, parent_path, serialized_dashboard, warehouse_id"}
        return _create_or_update_dashboard(display_name=display_name, parent_path=parent_path,
                                            serialized_dashboard=serialized_dashboard,
                                            warehouse_id=warehouse_id, publish=publish)
    elif act == "get":
        if not dashboard_id: return {"error": "requires: dashboard_id"}
        return _get_dashboard(dashboard_id=dashboard_id)
    elif act == "list": return {"dashboards": _list_dashboards()}
    elif act == "delete":
        if not dashboard_id: return {"error": "requires: dashboard_id"}
        _trash_dashboard(dashboard_id=dashboard_id)
        return {"status": "deleted", "dashboard_id": dashboard_id}
    elif act == "publish":
        if not dashboard_id or not warehouse_id: return {"error": "requires: dashboard_id, warehouse_id"}
        return _publish_dashboard(dashboard_id=dashboard_id, warehouse_id=warehouse_id,
                                   embed_credentials=embed_credentials)
    elif act == "unpublish":
        if not dashboard_id: return {"error": "requires: dashboard_id"}
        return _unpublish_dashboard(dashboard_id=dashboard_id)
    return {"error": f"Invalid action '{action}'"}


# ===========================================================================
# ── FILE / WORKSPACE TOOLS ──────────────────────────────────────────────────
# ===========================================================================

@mcp.tool(timeout=120)
def manage_volume_files(
    action: str, volume_path: str,
    local_path: str = None, local_destination: str = None,
    max_results: int = 500, recursive: bool = False,
    overwrite: bool = True, max_workers: int = 4,
) -> Dict:
    """Manage Unity Catalog Volume files: list, upload, download, delete, mkdir, get_info.

    volume_path format: /Volumes/catalog/schema/volume/path"""
    act = action.lower()
    if act == "list": return _list_volume_files(volume_path=volume_path, max_results=max_results)
    elif act == "upload":
        if not local_path: return {"error": "upload requires: local_path"}
        return _upload_to_volume(volume_path=volume_path, local_path=local_path,
                                  overwrite=overwrite, max_workers=max_workers)
    elif act == "download":
        if not local_destination: return {"error": "download requires: local_destination"}
        return _download_from_volume(volume_path=volume_path, local_destination=local_destination)
    elif act == "delete":
        return _delete_from_volume(volume_path=volume_path, recursive=recursive)
    elif act == "mkdir":
        _create_volume_directory(volume_path=volume_path)
        return {"success": True}
    elif act == "get_info":
        return _get_volume_file_metadata(volume_path=volume_path)
    return {"error": f"Invalid action '{action}'"}


@mcp.tool(timeout=120)
def manage_workspace_files(
    action: str, workspace_path: str,
    local_path: str = None, overwrite: bool = True,
    max_workers: int = 10, recursive: bool = False,
) -> Dict:
    """Manage workspace files: upload, delete.

    workspace_path format: /Workspace/Users/user@example.com/path/to/files"""
    act = action.lower()
    if act == "upload":
        if not local_path: return {"error": "upload requires: local_path"}
        return _upload_to_workspace(local_path=local_path, workspace_path=workspace_path,
                                     overwrite=overwrite, max_workers=max_workers)
    elif act == "delete":
        return _delete_from_workspace(workspace_path=workspace_path, recursive=recursive)
    return {"error": f"Invalid action '{action}'"}


@mcp.tool(timeout=30)
def manage_workspace(action: str, profile: str = None, host: str = None) -> Dict:
    """Manage active Databricks workspace connection.

    actions: status (current workspace), list (profiles from ~/.databrickscfg), switch (profile or host), login."""
    act = action.lower()
    w = get_workspace_client()

    if act == "status":
        cfg = w.config
        return {"host": cfg.host, "profile": getattr(cfg, "profile", "DEFAULT"),
                "username": getattr(cfg, "username", None)}
    elif act == "list":
        import configparser
        cfg_path = Path.home() / ".databrickscfg"
        if not cfg_path.exists(): return {"profiles": []}
        cp = configparser.ConfigParser()
        cp.read(cfg_path)
        return {"profiles": [{"name": s, "host": cp[s].get("host", "")} for s in cp.sections()]}
    elif act == "switch":
        if profile:
            os.environ["DATABRICKS_CONFIG_PROFILE"] = profile
            return {"profile": profile, "status": "switched"}
        elif host:
            os.environ["DATABRICKS_HOST"] = host
            return {"host": host, "status": "switched"}
        return {"error": "switch requires: profile or host"}
    return {"error": f"Invalid action '{action}'"}


# ===========================================================================
# ── USER TOOL ───────────────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool(timeout=30)
def get_current_user() -> Dict:
    """Get current Databricks user identity. Returns: {username (email), home_path}."""
    username = _get_current_username()
    home_path = f"/Workspace/Users/{username}" if username else None
    return {"username": username, "home_path": home_path}


# ===========================================================================
# ── PDF TOOL ────────────────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool
def generate_and_upload_pdf(
    html_content: str, filename: str,
    catalog: str, schema: str, volume: str = "raw_data",
    folder: str = None,
) -> Dict:
    """Convert complete HTML (with styles) to PDF and upload to Unity Catalog volume.

    Returns: {success, volume_path, error}."""
    return _generate_and_upload_pdf(html_content=html_content, filename=filename,
                                     catalog=catalog, schema=schema, volume=volume, folder=folder)


# ===========================================================================
# ── MANIFEST TOOLS ──────────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool(timeout=30)
def list_tracked_resources(type: str = None) -> Dict:
    """List resources tracked in project manifest. type: Filter by resource type.

    Returns: {resources: [...], count}."""
    if type:
        resources = _MANIFEST.get(type, [])
    else:
        resources = [r for lst in _MANIFEST.values() for r in lst]
    return {"resources": resources, "count": len(resources)}


@mcp.tool(timeout=60)
def delete_tracked_resource(
    type: str, resource_id: str,
    delete_from_databricks: bool = False,
) -> Dict:
    """Delete resource from manifest, optionally from Databricks too.

    delete_from_databricks: If True, deletes from Databricks first (default: False).
    Returns: {success, removed_from_manifest, deleted_from_databricks, error}."""
    deleted_from_databricks = False
    error = None

    if delete_from_databricks:
        deleter = _DELETERS.get(type)
        if deleter:
            try:
                deleter(resource_id)
                deleted_from_databricks = True
            except Exception as e:
                error = str(e)
        else:
            error = f"No deleter registered for type '{type}'"

    _remove_resource(type, resource_id)
    return {"success": True, "removed_from_manifest": True,
            "deleted_from_databricks": deleted_from_databricks, "error": error}


# ===========================================================================
# ── ENTRY POINT ─────────────────────────────────────────────────────────────
# ===========================================================================

if __name__ == "__main__":
    mcp.run()

