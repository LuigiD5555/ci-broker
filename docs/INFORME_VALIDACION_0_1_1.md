# CI Broker — Informe de validación v0.1.1

## Actualización verificada — 2026-11-04: P5 BASELINE PASS

La declaración de `BLOCKED` del informe original (2026-11-01) representa el estado de entonces. **P5 ya se ejecutó independientemente y aprobó sobre el commit exacto de estabilización**, sin fusionar la rama ni usar credenciales de proveedores:

- **Fuente validada:** `fix/stabilize-0.1.1` en SHA `81e9fef5d7dbe8ebf756a13c6733e3f265403261`, checkout fijado y comprobado mediante `git rev-parse HEAD`.
- **Intérprete:** Python **3.15.2**.
- **Comando:** `PYTHON=python bash scripts/validate_local.sh`.
- **Resultado:** **57/57 tests PASS**, `CI_BROKER_LOCAL_ACCEPTANCE_PASS`, `CI_BROKER_P5_BASELINE_ATTESTED`, salida 0.
- **Evidencia física del ejecutor independiente:** GitHub Actions público [run 41894707651, job 126801022124](https://github.com/LuigiD5555/ci-broker/actions/runs/41894707651/job/126801022124), `completed/success`.

En la rama experimental `feature/circleci-remote-cli-20261104`, el código de extensión fue validado sobre SHA `bc77f3d211c6f15b7cd64f0919db69ebd1b9aa0a`, dentro del mismo run, con **72/72** tests PASS en Python **3.13.17** y **3.15.2**, compilación e interfaz CLI verde; **MCP SDK 1.30.0 y 2.3.0** instalaron y registraron las cuatro herramientas esperadas (cuatro jobs adicionales, todos verdes). No se realizaron llamadas autenticadas a CircleCI ni se desplegó el receptor web; por tanto, **la aceptación remota end-to-end sigue pendiente**, y Z01/G5b de Extracta no queda certificada por esta evidencia.

**Alcance:** este addendum documenta el PASS offline de la fuente base indicada; no declara `main` fusionada ni garantiza que los adaptadores remotos ya funcionen con una cuenta de proveedor real. Las secciones históricas siguientes se conservan como registro del bloqueo previo.


**Fecha:** 2026-11-01  
**Rama de implementación:** `fix/stabilize-0.1.1`  
**Baseline auditado (externamente):** `43f57333ebcceab081f566ee5d86d445410e4eaf`  
**Estado de esta implementación:** **BLOCKED para declarar PASS**, por falta de ejecución completa de Python en el checkout exacto de la rama. El código está implementado; no hay evidencia de un nuevo PASS de tests.

## 1. Qué se implementó

- Admisión antes de `run --local --approve`, con las mismas restricciones declaradas que `plan`; rechazo a local deshabilitado o sin capacidades.
- `verified=false` excluido de proveedores remotos elegibles, sin ocultarlo del diagnóstico.
- Contabilidad conservadora: `max(settle, observed)` por ejecución y periodo de facturación; una observación menor no borra la liquidación previa. Observaciones contradictorias sobre el mismo run ID fallan explícitamente.
- Reservas y cierres idempotentes, revalidación de cuotas bajo `fcntl.flock`, y reservas sin liberación automática al cambiar de mes.
- Lectura de JSONL fail-closed (truncado, registro inválido, archivo no privado, symlink directo), comprobación de unidades finitas.
- `init` con comando portable `["python", "--version"]`; override explícito de catálogo inexistente rechazado.
- Timeout POSIX con sesión y grupo de procesos aislados; detención del grupo ante expiración.
- Nuevos tests offline de regresión, seguridad, multiproceso, plugins y grafo.
- Actualización de README, ARCHITECTURE, AGENTS, LLM_CONTEXT y script `scripts/validate_local.sh`.
- Versión de paquete y runtime actualizada a `0.1.1` como **candidata**, sin release ni PyPI.

## 2. Lo efectivamente comprobado en esta sesión

| Validación | Resultado | Alcance |
|---|---|---|
| Archivos y cambios en GitHub | CHECKED | Código y tests presentes en la rama |
| Condiciones críticas en fuente (admisión, locks, uso máximo, no dependencia de consumidores) | CHECKED | Comprobación estática de código, no ejecución |
| Ejemplos JSON del repositorio | CHECKED | Parseables como JSON |
| Versionado de pyproject/runtime | CHECKED | Ambos declaran 0.1.1 |
| Workflow GitHub Actions | CHECKED | Permanece solo manual |
| Suite de tests de la implementación | **NOT_EXECUTED** | No se dispone de checkout ejecutable de esta rama + Python 3.15.2 en el entorno del asistente |
| `scripts/validate_local.sh` | **NOT_EXECUTED** | Debe ejecutarse en el checkout de la rama |
| CI externos, despachos remotos o créditos | **NOT_EXECUTED** | Deliberadamente fuera de alcance |

Los **20/20 tests PASS con Python 3.15.2** corresponden al baseline auditado por el usuario, **no** a este candidato. Nunca deben atribuirse a la versión nueva.

## 3. Gate exacto de aceptación local

No requiere GitHub Actions, CircleCI, credenciales ni cuentas de proveedores.

```bash
cd /ruta/al/repositorio/ci-broker
git fetch origin fix/stabilize-0.1.1
git switch fix/stabilize-0.1.1
git rev-parse HEAD
python --version  # usar Python 3.15.2 dentro del entorno elegido
PYTHON=python bash scripts/validate_local.sh
echo "exit=$?"
```

El script configura sus cuentas y libro JSONL **en un directorio temporal**, ejecuta `unittest discover`, `compileall`, CLI help, `init`, `plan`, denegación sin permiso y ejecución local aprobada. Requiere `bash` y un intérprete Python >=3.11; la **aceptación principal** será con 3.15.2. Un final correcto imprime:

```text
CI_BROKER_LOCAL_ACCEPTANCE_PASS (remote CI NOT_EXECUTED)
```

También ejecutar por separado el conteo de pruebas y revisar que no queden procesos hijos ni cambios rastreados:

```bash
python -m unittest discover -s tests -v
git status --short
```

Capturar el SHA exacto, salida completa y códigos de salida. No contar como PASS la existencia de tests, comandos sin ejecutar ni un resultado de otra rama. No hacer un merge a `main` solo por una suite verde; revisar los riesgos y el diff primero.

## 4. Estado P0–P5

| Paquete | Estado | Evidencia que todavía requiere |
|---|---|---|
| P0: tests de regresión | IMPLEMENTED / TEST_PENDING | Ejecutar y confirmar regresiones en 3.15.2; el rojo sobre el baseline no se ejecutó aquí |
| P1: admisión unificada | IMPLEMENTED / TEST_PENDING | Tests de local disabled, capacidades, proveedor no verificado |
| P2: ledger conservador | IMPLEMENTED / TEST_PENDING | Tests multiproceso, liquidación/observación, idempotencia y mes |
| P3: portabilidad/seguridad | IMPLEMENTED / TEST_PENDING | Tests de symlink, timeout descendiente, configuración inválida |
| P4: compatibilidad/docs | IMPLEMENTED / TEST_PENDING | Copiar ejemplos, ejecutar harness, revisar límites documentados |
| P5: aceptación final | **BLOCKED** | Salida real y código 0 de `scripts/validate_local.sh` en checkout exacto |

**Criterio de cierre:** actualizar este informe a PASS solo después de ejecutar realmente el harness completo, verificar sus resultados y demostrar INV-01..INV-16. En caso de error, dejar FAIL documentando el caso concreto; no iniciar nuevas funciones por inercia.

## 5. Límites y riesgos remanentes

- La contabilidad es conservadora, pero no implementa ajustes negativos auditables ni sincroniza facturación de terceros.
- El ledger se coordina por procesos en un mismo host; no es una base de datos distribuida.
- Los scripts propios de los proyectos se ejecutan con confianza explícita, no en una sandbox.
- Las capacidades de una máquina son declaradas por el operador; v0.1.1 no las descubre automáticamente.
- No hay despacho remoto de CI, pipelines generados, scheduler paralelo ni selección automática por impacto.
- No se modifica Extracta; el núcleo sigue siendo agnóstico, como extensión/vendor.
