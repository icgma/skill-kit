#!/usr/bin/env python3
"""Call Wind through agent-gw or datasource service."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import requests

DATA_SOURCE_NAME = "wind"
DEFAULT_TIMEOUT = 60.0
DEFAULT_AGENT_GW_BASE_URL = "https://agent-gw-dev.dev.kimi.team/coding"
DEFAULT_AGENT_GW_CONFIG_PATH = Path.home() / ".kimi" / "agent-gw.json"


def _ensure_agent_gw():
    """确保 agent-gw SDK 已安装；未安装时自动尝试安装。"""
    try:
        import agent_gw
        return
    except ModuleNotFoundError:
        pass

    import subprocess
    import sys

    package_url = "git+ssh://git@dev.msh.team/leixun/agent-gw-pysdk.git@v0.1.0"
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", package_url],
            timeout=180,
        )
    except Exception as exc:
        raise SystemExit(
            f"Failed to install agent-gw automatically: {exc}. "
            f"Please install manually: pip install \"{package_url}\""
        ) from exc

    try:
        import agent_gw  # noqa: F401
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "agent-gw still not importable after install. "
            'Please install manually: pip install "git+ssh://git@dev.msh.team/leixun/agent-gw-pysdk.git@v0.1.0"'
        ) from exc


def _client_cls():
    _ensure_agent_gw()
    from agent_gw import AgentGwClient, AgentGwError
    return AgentGwClient, AgentGwError


def _json_obj(text: str) -> Dict[str, Any]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise argparse.ArgumentTypeError("JSON value must be an object")
    return data


def _load_agent_gw_config() -> Dict[str, Any]:
    if not DEFAULT_AGENT_GW_CONFIG_PATH.is_file():
        return {}
    try:
        data = json.loads(DEFAULT_AGENT_GW_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Invalid JSON in {DEFAULT_AGENT_GW_CONFIG_PATH}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise SystemExit(f"Invalid config in {DEFAULT_AGENT_GW_CONFIG_PATH}: expected a JSON object")
    return data


def _response_json(response: requests.Response) -> Dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        snippet = response.text.strip().replace("\n", " ")[:300]
        raise RuntimeError(
            f"HTTP {response.status_code} returned non-JSON body: {snippet or '<empty>'}"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"HTTP {response.status_code} returned non-object JSON payload")
    return data


def _resolve_transport(args: argparse.Namespace) -> str:
    if args.transport != "auto":
        return args.transport
    if getattr(args, "env_name", "") or os.getenv("ENV", "").strip():
        return "datasource"
    return "agent-gw"


def _resolve_agent_gw_api_key(args: argparse.Namespace) -> str:
    config = _load_agent_gw_config()
    api_key = (
        getattr(args, "api_key", "")
        or os.getenv("KIMI_API_KEY", "")
        or str(config.get("api_key", "")).strip()
    )
    if not api_key:
        raise SystemExit(
            "Missing KIMI API key. Provide --api-key, set KIMI_API_KEY, "
            f"or add api_key to {DEFAULT_AGENT_GW_CONFIG_PATH}."
        )
    return api_key


def _resolve_agent_gw_base_url(args: argparse.Namespace) -> str:
    config = _load_agent_gw_config()
    return (
        getattr(args, "base_url", "")
        or os.getenv("KIMI_BASE_URL", "")
        or str(config.get("base_url", "")).strip()
        or DEFAULT_AGENT_GW_BASE_URL
    )


def _agent_gw_client_kwargs(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "api_key": _resolve_agent_gw_api_key(args),
        "base_url": _resolve_agent_gw_base_url(args),
        "timeout": DEFAULT_TIMEOUT,
    }


def _resolve_datasource_target(args: argparse.Namespace) -> tuple[str, str]:
    env_name = (getattr(args, "env_name", "") or os.getenv("ENV", "local")).strip().lower()
    prefix = env_name.upper()
    base_url = (
        getattr(args, "base_url", "")
        or os.getenv("DATA_SOURCE_BASE_URL", "")
        or os.getenv(f"{prefix}_BASE_URL", "")
    ).strip()
    api_key = (
        getattr(args, "api_key", "")
        or os.getenv("DATA_SOURCE_API_KEY", "")
        or os.getenv(f"{prefix}_API_KEY", "")
    ).strip()
    if not base_url or not api_key:
        raise SystemExit(
            f"Missing datasource config for env={env_name}. "
            f"Provide --base-url/--api-key or set {prefix}_BASE_URL and {prefix}_API_KEY."
        )
    return base_url.rstrip("/"), api_key


def _load_params(args: argparse.Namespace) -> Dict[str, Any]:
    if args.params_json and args.params_file:
        raise SystemExit("Use only one of --params-json or --params-file")
    if args.params_file:
        return _json_obj(Path(args.params_file).read_text(encoding="utf-8"))
    if args.params_json:
        return _json_obj(args.params_json)
    return {}


def _texts(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def _print_saved_files(raw: Dict[str, Any]) -> None:
    for file_info in raw.get("files") or []:
        if not isinstance(file_info, dict) or not file_info.get("name"):
            continue
        path = Path(str(file_info["name"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(file_info.get("content", "")), encoding="utf-8")


def _print_call_result(raw: Dict[str, Any], api_name: str, data_source: str) -> int:
    if not raw.get("is_success"):
        error = raw.get("error") or {}
        assistant_errors = _texts(error.get("assistant") if isinstance(error, dict) else None)
        user_errors = _texts(error.get("user") if isinstance(error, dict) else None)
        message = "\n".join(user_errors or assistant_errors)
        if not message:
            message = f"Error calling API '{api_name}' from data source '{data_source}'"
        print(message, file=sys.stderr)
        return 1

    _print_saved_files(raw)
    result = raw.get("result") or {}
    assistant_texts = _texts(result.get("assistant") if isinstance(result, dict) else None)
    print("\n".join(assistant_texts))
    return 0


def _describe_via_datasource(args: argparse.Namespace) -> int:
    base_url, api_key = _resolve_datasource_target(args)
    try:
        response = requests.post(
            f"{base_url}/get_data_source_info",
            json={"name": args.data_source},
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        raw = _response_json(response)
    except Exception as exc:
        print(
            f"Unexpected error describing data source '{args.data_source}' via datasource service: {exc}",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(raw, ensure_ascii=False, indent=2))
    return 0


def _describe_via_agent_gw(args: argparse.Namespace) -> int:
    agent_gw_client, agent_gw_error = _client_cls()
    try:
        with agent_gw_client(**_agent_gw_client_kwargs(args)) as client:
            resp = client.tools.get_data_source_desc({"name": args.data_source})
            print(resp.text)
    except agent_gw_error as exc:
        print(f"Error describing data source '{args.data_source}': {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"Unexpected error describing data source '{args.data_source}': {exc}",
            file=sys.stderr,
        )
        return 1
    return 0


def _call_via_datasource(args: argparse.Namespace, payload: Dict[str, Any]) -> int:
    base_url, api_key = _resolve_datasource_target(args)
    try:
        response = requests.post(
            f"{base_url}/call_data_source_tool",
            json=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        raw = _response_json(response)
    except Exception as exc:
        print(
            f"Unexpected error calling API '{args.api_name}' from data source '{args.data_source}' via datasource service: {exc}",
            file=sys.stderr,
        )
        return 1
    return _print_call_result(raw, args.api_name, args.data_source)


def _call_via_agent_gw(args: argparse.Namespace, payload: Dict[str, Any]) -> int:
    agent_gw_client, agent_gw_error = _client_cls()
    try:
        with agent_gw_client(**_agent_gw_client_kwargs(args)) as client:
            resp = client.tools.call_data_source_tool(payload)
    except agent_gw_error as exc:
        print(
            f"Error calling API '{args.api_name}' from data source '{args.data_source}': {exc}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(
            f"Unexpected error calling API '{args.api_name}' from data source '{args.data_source}': {exc}",
            file=sys.stderr,
        )
        return 1
    return _print_call_result(resp.raw, args.api_name, args.data_source)


def describe(args: argparse.Namespace) -> int:
    if _resolve_transport(args) == "datasource":
        return _describe_via_datasource(args)
    return _describe_via_agent_gw(args)


def call(args: argparse.Namespace) -> int:
    api_params = _load_params(args)
    payload = {
        "data_source_name": args.data_source,
        "api_name": args.api_name,
        "params": api_params,
    }
    if _resolve_transport(args) == "datasource":
        return _call_via_datasource(args, payload)
    return _call_via_agent_gw(args, payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    describe_parser = subparsers.add_parser("describe", help="Print Wind Markdown docs")
    describe_parser.add_argument("--data-source", default=DATA_SOURCE_NAME)
    describe_parser.add_argument(
        "--transport",
        choices=["auto", "agent-gw", "datasource"],
        default="auto",
        help="Routing mode. Defaults to datasource when --env/ENV is set, otherwise agent-gw.",
    )
    describe_parser.add_argument(
        "--env",
        dest="env_name",
        default="",
        help="Datasource env name for direct mode: local/test/prod. Falls back to ENV.",
    )
    describe_parser.add_argument(
        "--api-key",
        default="",
        help="API key for the selected transport. Datasource mode also reads <ENV>_API_KEY.",
    )
    describe_parser.add_argument(
        "--base-url",
        default="",
        help="Base URL for the selected transport. Datasource mode also reads <ENV>_BASE_URL.",
    )
    describe_parser.set_defaults(func=describe)

    call_parser = subparsers.add_parser("call", help="Call a Wind API")
    call_parser.add_argument("--data-source", default=DATA_SOURCE_NAME)
    call_parser.add_argument(
        "--transport",
        choices=["auto", "agent-gw", "datasource"],
        default="auto",
        help="Routing mode. Defaults to datasource when --env/ENV is set, otherwise agent-gw.",
    )
    call_parser.add_argument(
        "--env",
        dest="env_name",
        default="",
        help="Datasource env name for direct mode: local/test/prod. Falls back to ENV.",
    )
    call_parser.add_argument(
        "--api-key",
        default="",
        help="API key for the selected transport. Datasource mode also reads <ENV>_API_KEY.",
    )
    call_parser.add_argument(
        "--base-url",
        default="",
        help="Base URL for the selected transport. Datasource mode also reads <ENV>_BASE_URL.",
    )
    call_parser.add_argument("--api-name", required=True)
    call_parser.add_argument("--params-json", help="API params as a JSON object")
    call_parser.add_argument("--params-file", help="Path to a JSON object with API params")
    call_parser.set_defaults(func=call)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
