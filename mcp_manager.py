#!/usr/bin/env python3
"""Single entrypoint for the Automation multi-agent workspace."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
AGENTS_CONFIG = ROOT / "config" / "agents.json"
TOOLS_CONFIG = ROOT / "config" / "mcp-tools.json"
UI_SERVER = ROOT / "ui" / "server.py"
UI_INDEX = ROOT / "docs" / "po-sm-operations-ui.html"


@dataclass(frozen=True)
class AgentConfig:
    name: str
    enabled: bool
    path: Path
    command: list[str]
    description: str


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    enabled: bool
    path: Path
    transport: str
    command: list[str]
    description: str


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_agents() -> list[AgentConfig]:
    raw_config = load_json(AGENTS_CONFIG)
    agents = []

    for raw_agent in raw_config.get("agents", []):
        agent_path = ROOT / raw_agent["path"]
        agents.append(
            AgentConfig(
                name=raw_agent["name"],
                enabled=bool(raw_agent.get("enabled", False)),
                path=agent_path,
                command=list(raw_agent.get("command", [])),
                description=raw_agent.get("description", ""),
            )
        )

    return agents


def load_mcp_servers() -> list[McpServerConfig]:
    raw_config = load_json(TOOLS_CONFIG)
    servers = []

    for raw_server in raw_config.get("servers", []):
        server_path = ROOT / raw_server["path"]
        servers.append(
            McpServerConfig(
                name=raw_server["name"],
                enabled=bool(raw_server.get("enabled", False)),
                path=server_path,
                transport=raw_server.get("transport", "stdio"),
                command=list(raw_server.get("command", [])),
                description=raw_server.get("description", ""),
            )
        )

    return servers


def validate() -> int:
    errors: list[str] = []

    try:
        agents = load_agents()
        servers = load_mcp_servers()
    except Exception as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1

    agent_names = set()
    for agent in agents:
        if agent.name in agent_names:
            errors.append(f"Duplicate agent name: {agent.name}")
        agent_names.add(agent.name)

        if not agent.path.exists():
            errors.append(f"Agent path does not exist for {agent.name}: {agent.path}")

        if agent.enabled and not agent.command:
            errors.append(f"Enabled agent has no command: {agent.name}")

    server_names = set()
    for server in servers:
        if server.name in server_names:
            errors.append(f"Duplicate MCP server name: {server.name}")
        server_names.add(server.name)

        if not server.path.exists():
            errors.append(f"MCP server path does not exist for {server.name}: {server.path}")

        if server.transport not in {"stdio", "http+sse", "streamable-http"}:
            errors.append(f"Unsupported MCP transport for {server.name}: {server.transport}")

        if server.enabled and server.transport == "stdio" and not server.command:
            errors.append(f"Enabled stdio MCP server has no command: {server.name}")

    if not UI_SERVER.exists():
        errors.append(f"UI server does not exist: {UI_SERVER}")

    if not UI_INDEX.exists():
        errors.append(f"UI page does not exist: {UI_INDEX}")

    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Configuration is valid.")
    return 0


def list_agents() -> int:
    agents = load_agents()

    if not agents:
        print("No agents registered yet.")
        return 0

    for agent in agents:
        state = "enabled" if agent.enabled else "disabled"
        print(f"{agent.name} [{state}]")
        if agent.description:
            print(f"  {agent.description}")
        print(f"  path: {agent.path.relative_to(ROOT)}")
        print(f"  command: {' '.join(agent.command) if agent.command else '(not set)'}")

    return 0


def list_mcp_servers() -> int:
    servers = load_mcp_servers()

    if not servers:
        print("No MCP servers registered yet.")
        return 0

    for server in servers:
        state = "enabled" if server.enabled else "disabled"
        print(f"{server.name} [{state}]")
        if server.description:
            print(f"  {server.description}")
        print(f"  path: {server.path.relative_to(ROOT)}")
        print(f"  transport: {server.transport}")
        print(f"  command: {' '.join(server.command) if server.command else '(not set)'}")

    return 0


def start_agents(
    dry_run: bool = False,
    *,
    start_ui: bool = False,
    ui_host: str = "127.0.0.1",
    ui_port: int = 8787,
) -> int:
    agents = [agent for agent in load_agents() if agent.enabled]

    if not agents and not start_ui:
        print("No enabled agents to start. Register agents in Automation/config/agents.json.")
        return 0

    processes: list[tuple[AgentConfig, subprocess.Popen[str]]] = []
    ui_process: subprocess.Popen[str] | None = None

    if start_ui:
        ui_command = [sys.executable, str(UI_SERVER), "--host", ui_host, "--port", str(ui_port)]
        if dry_run:
            print(f"Would start po-sm-operations-ui: {' '.join(ui_command)}")
        else:
            print(f"Starting PO/SM Operations UI at http://{ui_host}:{ui_port}/ ...")
            ui_process = subprocess.Popen(
                ui_command,
                cwd=ROOT,
                text=True,
            )

    for agent in agents:
        if dry_run:
            print(f"Would start {agent.name}: {' '.join(agent.command)}")
            continue

        print(f"Starting {agent.name}...")
        process = subprocess.Popen(
            agent.command,
            cwd=agent.path,
            text=True,
        )
        processes.append((agent, process))

    if dry_run:
        return 0

    exit_code = 0
    try:
        if ui_process:
            result = ui_process.wait()
            if result != 0:
                print(f"po-sm-operations-ui exited with code {result}", file=sys.stderr)
                exit_code = result

        for agent, process in processes:
            result = process.wait()
            if result != 0:
                print(f"{agent.name} exited with code {result}", file=sys.stderr)
                exit_code = result
    except KeyboardInterrupt:
        print("Stopping agents...")
        if ui_process:
            ui_process.terminate()
        for _, process in processes:
            process.terminate()
        exit_code = 130

    return exit_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start and manage Automation agents.")
    parser.add_argument("--list", action="store_true", help="List configured agents.")
    parser.add_argument("--list-mcp", action="store_true", help="List configured MCP servers.")
    parser.add_argument("--validate", action="store_true", help="Validate workspace configuration.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would start.")
    parser.add_argument("--ui", action="store_true", help="Start the local PO/SM operations UI.")
    parser.add_argument("--ui-host", default=os.getenv("AUTOMATION_UI_HOST", "127.0.0.1"), help="Host for the local PO/SM operations UI.")
    parser.add_argument("--ui-port", type=int, default=int(os.getenv("AUTOMATION_UI_PORT", "8787")), help="Port for the local PO/SM operations UI.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.list:
        return list_agents()

    if args.list_mcp:
        return list_mcp_servers()

    if args.validate:
        return validate()

    return start_agents(
        dry_run=args.dry_run,
        start_ui=args.ui,
        ui_host=args.ui_host,
        ui_port=args.ui_port,
    )


if __name__ == "__main__":
    raise SystemExit(main())
