"""Gestion de tuneles SSH y conexiones Oracle (RAWDB / DM_DBA).

Copiado tal cual de generar-imprimibles/versiones-vistas-imprimibles. No trae
copia propia de la clave SSH ni del Instant Client (pesado, ~500MB, y la
clave es sensible): por defecto ambos se resuelven via ruta relativa a
../generar-imprimibles (ver .env.example).
"""
from __future__ import annotations

import atexit
import os
import socket
import subprocess
import threading
import time

import oracledb
from dotenv import load_dotenv

from config.environments import get_environment

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))


def _resolve(path: str) -> str:
    """Convierte una ruta relativa (del .env) en absoluta respecto al proyecto."""
    if not path:
        return path
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)


def _default_ssh_binary() -> str:
    win_ssh = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", "OpenSSH", "ssh.exe"
    )
    return win_ssh if os.path.exists(win_ssh) else "ssh"


DB_USER = os.getenv("DB_USER", "DM_DBA")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
SSH_BASTION = os.getenv("SSH_BASTION", "132.145.197.201")
SSH_PORT = os.getenv("SSH_PORT", "22")
SSH_USER = os.getenv("SSH_USER", "ctgds")
SSH_KEY_PATH = _resolve(
    os.getenv("SSH_KEY_PATH", os.path.join("..", "generar-imprimibles", "keys", "ctgds"))
)
SSH_BINARY = os.getenv("SSH_BINARY", "") or _default_ssh_binary()
ORACLE_CLIENT_LIB = _resolve(
    os.getenv(
        "ORACLE_CLIENT_LIB",
        os.path.join("..", "generar-imprimibles", "oracle", "instantclient_19_13"),
    )
)

TUNNEL_READY_TIMEOUT = 20  # segundos que esperamos a que el puerto local escuche
DB_CONNECT_TIMEOUT = 15

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_thick_initialized = False


def _init_thick_mode() -> None:
    global _thick_initialized
    if _thick_initialized:
        return
    if not os.path.isdir(ORACLE_CLIENT_LIB):
        raise RuntimeError(
            "Las bases GDS requieren modo thick (Native Network Encryption) y no se "
            f"encontro el Oracle Instant Client en {ORACLE_CLIENT_LIB!r}. "
            "Definí ORACLE_CLIENT_LIB en .env (por defecto apunta a "
            "../generar-imprimibles/oracle/instantclient_19_13)."
        )
    oracledb.init_oracle_client(lib_dir=ORACLE_CLIENT_LIB)
    _thick_initialized = True


def _port_is_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((host, port)) == 0


class _Tunnel:
    """Reenvio 127.0.0.1:local_port -> remote_ip:1521 via bastion (ssh -L)."""

    def __init__(self, local_port: int, remote_ip: str):
        self.local_port = local_port
        self.remote_ip = remote_ip
        self.process: subprocess.Popen | None = None

    def _build_cmd(self) -> list[str]:
        return [
            SSH_BINARY,
            "-N",
            "-o", "LogLevel=ERROR",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-p", str(SSH_PORT),
            "-L", f"{self.local_port}:{self.remote_ip}:1521",
            "-i", SSH_KEY_PATH,
            f"{SSH_USER}@{SSH_BASTION}",
        ]

    def ensure(self) -> None:
        if self.process and self.process.poll() is None:
            return
        # Si el puerto ya escucha (p.ej. corriste otra de estas apps), reusamos.
        if _port_is_open(self.local_port):
            return
        if not os.path.exists(SSH_KEY_PATH):
            raise RuntimeError(
                f"No se encontro la clave SSH en {SSH_KEY_PATH!r}. "
                "Definí SSH_KEY_PATH en .env (por defecto apunta a "
                "../generar-imprimibles/keys/ctgds)."
            )
        self.process = subprocess.Popen(
            self._build_cmd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=_CREATE_NO_WINDOW,
        )
        deadline = time.time() + TUNNEL_READY_TIMEOUT
        while time.time() < deadline:
            if self.process.poll() is not None:
                err = (self.process.stderr.read() or b"").decode(errors="replace").strip()
                raise RuntimeError(f"El tunel SSH termino inesperadamente: {err}")
            if _port_is_open(self.local_port):
                return
            time.sleep(0.4)
        self.close()
        raise TimeoutError(
            f"El tunel SSH no quedo listo en {TUNNEL_READY_TIMEOUT}s (puerto {self.local_port})."
        )

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None


_tunnels: dict[str, _Tunnel] = {}
_lock = threading.Lock()


def _get_tunnel(env: dict) -> _Tunnel:
    with _lock:
        tunnel = _tunnels.get(env["key"])
        if tunnel is None:
            tunnel = _Tunnel(env["local_port"], env["remote_ip"])
            _tunnels[env["key"]] = tunnel
        tunnel.ensure()
        return tunnel


def get_connection(env_key: str) -> oracledb.Connection:
    """Abre (o reusa) el tunel del ambiente y devuelve una conexion oracledb."""
    if not DB_PASSWORD:
        raise RuntimeError("DB_PASSWORD no esta configurado en .env.")
    env = get_environment(env_key)
    _init_thick_mode()
    _get_tunnel(env)
    dsn = oracledb.makedsn("localhost", env["local_port"], service_name=env["service_name"])
    return oracledb.connect(
        user=DB_USER,
        password=DB_PASSWORD,
        dsn=dsn,
        tcp_connect_timeout=DB_CONNECT_TIMEOUT,
    )


@atexit.register
def _close_all() -> None:
    for tunnel in _tunnels.values():
        tunnel.close()
