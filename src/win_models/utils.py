from __future__ import annotations

import argparse

from .common import echo, powershell, stop_process_names
from .config import DEFAULT_LITERT_PORT, DEFAULT_LLAMA_PORT


def status(args: argparse.Namespace) -> None:
    echo("Processes")
    print(
        powershell(
            "Get-Process llama-server,litert-lm -ErrorAction SilentlyContinue "
            "| Select-Object Id,ProcessName,StartTime,CPU | Format-Table -AutoSize",
            check=False,
        ).rstrip()
    )
    echo("\nPorts")
    print(
        powershell(
            f"Get-NetTCPConnection -LocalPort {args.llama_port},{args.litert_port} -ErrorAction SilentlyContinue "
            "| Select-Object LocalAddress,LocalPort,State,OwningProcess | Format-Table -AutoSize",
            check=False,
        ).rstrip()
    )
    echo("\nGPU")
    print(
        powershell(
            "nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu --format=csv,noheader",
            check=False,
        ).rstrip()
    )
    echo("\nLAN IPs")
    print(
        powershell(
            "Get-NetIPAddress -AddressFamily IPv4 "
            "| Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } "
            "| Select-Object IPAddress,InterfaceAlias | Format-Table -AutoSize",
            check=False,
        ).rstrip()
    )


def stop(args: argparse.Namespace) -> None:
    stop_process_names(["llama-server", "litert-lm"])
    echo("Stopped llama-server and litert-lm processes.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="win-models utils")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("status")
    p.add_argument("--llama-port", type=int, default=DEFAULT_LLAMA_PORT)
    p.add_argument("--litert-port", type=int, default=DEFAULT_LITERT_PORT)
    p.set_defaults(func=status)

    p = sub.add_parser("stop")
    p.set_defaults(func=stop)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)

