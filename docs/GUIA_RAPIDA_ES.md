# Guía rápida de CI Broker (español)

CI Broker es una herramienta independiente de los proyectos. **No depende de Extracta**. Se instala una vez, se configura cada proyecto por separado y se comparte el registro de consumo de CI entre proyectos en la misma máquina.

## Primer uso

```bash
python3 -m pip install -e .
cd /ruta/a/mi-proyecto
ci-broker init
```

Edita `.ci-broker/project.json` y reemplaza el comando de ejemplo por tus pruebas reales. Después:

```bash
ci-broker plan --profile light
ci-broker run --profile light --local --approve
```

**Plan** solo calcula, no ejecuta nada. **Run** ejecuta únicamente comandos locales del propio proyecto y exige `--approve`. No hay despacho automático de pruebas a la nube.

## Ejemplo reutilizable

```json
{
  "schema": 1,
  "project_id": "mi-repositorio",
  "tasks": {
    "lint": {
      "command": ["python", "-m", "compileall", "-q", "src"],
      "expected_minutes": 1,
      "timeout_minutes": 3,
      "capabilities": ["python"]
    },
    "tests": {
      "needs": ["lint"],
      "command": ["python", "-m", "unittest", "discover", "-s", "tests"],
      "expected_minutes": 4,
      "timeout_minutes": 10,
      "capabilities": ["python"]
    }
  },
  "profiles": {"light": ["tests"]}
}
```

Al elegir `tests`, el motor incluye `lint` una sola vez por el grafo de dependencias. Los comandos son listas de argumentos, no cadenas de shell. Cada proyecto define su lenguaje y versión mediante sus propios scripts o herramientas; el núcleo no los impone.

## Organización

- `src/ci_broker/`: biblioteca y CLI agnósticas.
- `.ci-broker/project.json`: configuración que vive **en el repositorio consumidor**.
- `~/.config/ci-broker/providers.json`: cuentas y capacidades del operador.
- `~/.local/state/ci-broker/ledger.jsonl`: registro global local de consumo y reservas.
- `examples/`: ejemplos Python, Rust, Go, Node y Extracta.
- `docs/LLM_CONTEXT.md`: documento corto para copiar como contexto a un agente.
- `docs/ARCHITECTURE.md`: contratos y límites.

No marques `verified=true` para un servicio externo sin consultar su facturación real. Una reserva no inicia trabajos de CI ni prueba que las máquinas remotas estén configuradas.

Para ver la documentación principal y ejemplos paso a paso, lee [README.md](../README.md).
