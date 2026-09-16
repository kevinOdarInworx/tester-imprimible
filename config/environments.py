"""Topologia de ambientes GDS / RAWDB.

Copia de la de generar-imprimibles/versiones-vistas-imprimibles, con
LOCAL_PORT propios para poder correr las tres apps al mismo tiempo.
"""

# key -> definicion de ambiente
ENVIRONMENTS = {
    "DEV": {
        "label": "GDS DEV (RAWDB)",
        "local_port": 15221,
        "remote_ip": "10.70.47.66",
        "service_name": "rawdb.sngdstools.vcngdsshared.oraclevcn.com",
    },
    "UAT": {
        "label": "GDS UAT (RAWDB)",
        "local_port": 15222,
        "remote_ip": "10.70.47.101",
        "service_name": "rawdb.sngdstools.vcngdsshared.oraclevcn.com",
    },
    "SIT": {
        "label": "GDS SIT / PREPROD (RAWDB)",
        "local_port": 15223,
        "remote_ip": "10.70.36.34",
        "service_name": "RAWDB.sninsordbsit.gdsinsornonprd.oraclevcn.com",
    },
    "STST": {
        "label": "GDS STST (RAWDB)",
        "local_port": 15224,
        "remote_ip": "10.70.36.6",
        "service_name": "RAWDB.sninsordbstst.gdsinsornonprd.oraclevcn.com",
    },
    "PROD": {
        "label": "GDS PROD (RAWDB)",
        "local_port": 15225,
        "remote_ip": "10.70.40.9",
        "service_name": "rawdb.sndbprod.vcngdsinsorprod.oraclevcn.com",
    },
}


def get_environment(key: str) -> dict:
    key = (key or "").upper()
    if key not in ENVIRONMENTS:
        raise KeyError(f"Ambiente desconocido: {key}")
    env = dict(ENVIRONMENTS[key])
    env["key"] = key
    return env
