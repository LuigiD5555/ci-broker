# CI Broker — Plan maestro de estabilización v0.1.1

> **ESTADO: PLAN APROBABLE, SIN IMPLEMENTAR.** Este documento organiza el trabajo pendiente; no implica que existan correcciones ni pruebas ejecutadas sobre una futura versión.
>
> **Repositorio:** `LuigiD5555/ci-broker`  
> **Rama de trabajo:** `feature/agnostic-core`  
> **Baseline auditado:** `43f57333ebcceab081f566ee5d86d445410e4eaf`  
> **Python principal de aceptación:** `3.15.2`  
> **Restricciones:** cero ejecuciones de GitHub Actions u otros CI de pago, cero disparos remotos, cero cambios a Extracta, cero fusiones/force push a `main`.

## 1. Resultado que queremos conseguir

Una **v0.1.1 local-first, independiente de proyectos y verificable** donde:

1. Toda ejecución local pase por la misma política de admisión que la planificación.
2. Ninguna contabilización o conciliación ambigua reduzca el consumo protegido sin una corrección explícita, trazable y autorizada.
3. Un proveedor remoto no verificado nunca sea mostrado como ejecutor autorizado ni permita reservar créditos.
4. Los errores de configuración sean visibles y no se reemplacen silenciosamente por valores predeterminados.
5. Cada proyecto pueda mover su configuración entre máquinas sin rutas absolutas de un entorno virtual anterior.
6. Los timeouts de los comandos locales no dejen procesos hijos de pruebas ejecutándose sin control.
7. La compatibilidad del libro JSONL existente y del esquema de proyectos v1 esté protegida por pruebas.
8. La validación sea local, offline, reproducible y cubra decisiones positivas y negativas.

**No hay un número abierto de iteraciones.** Hay cinco paquetes de trabajo con criterios de salida cerrados; el sexto es la revisión final, sin incorporar funciones nuevas.

## 2. Alcance y límites

**Dentro de v0.1.1:**
- Política única de elegibilidad, selección y ejecución.
- Identidad de proveedores/cuentas y presupuestos comprobados.
- Estado de reservas y consumos, duplicados, reconciliación conservadora y límites de mes.
- CLI, validación estricta de configuración, rutas portables y salida estructurada coherente.
- Finalización de grupos de procesos al expirar un timeout, con cuidado de POSIX.
- Reforzar integridad del ledger, permisos, pruebas de concurrencia multiproceso y escenarios adversos.
- Documentar reglas y actualizar ejemplos sin contaminar el núcleo con Extracta.

**Fuera de v0.1.1, explícitamente:**
- Despacho remoto a GitHub/CircleCI/GitLab/etc., sincronización de facturación en vivo y gestión de secretos.
- Selección incremental de pruebas por archivos, DAG paralelo, machine learning para estimación y optimización multidimensional avanzada.
- SQLite / backend de coordinación distribuida, multi-host o agentes persistentes.
- Migración o integración automática con Extracta y BPFW.
- Publicación en PyPI, release oficial, modificación de `main`.
- Nuevas configuraciones de proveedores u oportunidades de cuota: no son necesarias para corregir las invariantes.

## 3. Invariantes obligatorias (fuente de verdad del código y de los tests)

| ID | Invariante | Comprobación de aceptación |
|---|---|---|
| INV-01 | `run --local` no ejecuta si `plan` rechaza al ejecutor local por `enabled=false`, capacidad, duración o presupuesto. | Mismo perfil, mismas restricciones, ejecución denegada sin lanzar ningún proceso |
| INV-02 | `--approve` es necesario, pero **no suficiente** para saltarse la política. | `--approve` con proveedor inelegible sigue fallando |
| INV-03 | Una cuenta remota `verified=false` puede verse en diagnósticos, pero jamás es `eligible=true`, `selected` ni reservable. | Plan independiente sin proveedor seleccionado; reserva denegada |
| INV-04 | `plan` no consume créditos ni ejecuta comandos; `reserve` tampoco dispara CI. | Todas las pruebas de planificación trabajan sin red ni procesos de prueba |
| INV-05 | Un mismo `(account, provider, run_ref)` no se contabiliza dos veces por lecturas duplicadas, pero tampoco se puede **subcontar** por una observación inferior. | `settle=8`, `observed=3` implica consumo protegido >=8 hasta ajuste explícito |
| INV-06 | La reserva usa el peor caso y un margen de cuota; la decisión definitiva se revalida bajo el lock. | Competidores simultáneos no exceden la cuota protegida |
| INV-07 | Una reserva pendiente nunca se libera por TTL ni por cambio de mes. | Reserva de noviembre sigue bloqueando presupuesto hasta cierre confirmado en diciembre |
| INV-08 | Cerrar, cancelar y registrar observaciones tiene semántica explícita e idempotente. | Repeticiones iguales no cambian consumo; conflictos se rechazan o conservan máximo sin ambigüedad |
| INV-09 | Los límites y números inválidos no se convierten en cálculos admisibles. | Rechazo de negativos, cero donde no procede, `NaN`, `inf`, unidades incompatibles, márgenes fuera de rango |
| INV-10 | Un `CI_BROKER_ACCOUNTS` **definido** pero inexistente o ilegible es error. | Fail-closed; el default local solo existe cuando no se proporcionó override |
| INV-11 | `init` crea configuración portable. | No incluye ruta absoluta de la venv ni de `sys.executable` |
| INV-12 | Expiración del timeout detiene el grupo local controlado, no únicamente su proceso padre. | Test de proceso hijo, verificación de que no queda en ejecución |
| INV-13 | Un JSONL truncado, alterado o mal formado jamás se interpreta como ledger vacío. | Bloqueo explícito; sin consumir ni liberar presupuesto |
| INV-14 | Los plugins solo cargan cuando están aprobados explícitamente; la configuración declarativa no ejecuta Python. | Tests con plugin desconocido e importación espía |
| INV-15 | No hay ninguna importación del consumidor (`extracta`, BPFW, etc.) en `src/ci_broker`. | Test de arquitectura y uso contra dos proyectos sintéticos distintos |
| INV-16 | Una falla de una tarea impide ejecutar sus sucesoras; un nodo compartido se ejecuta una vez por plan. | DAG diamante, ciclo, nodo inexistente y fallo intermedio |

La definición de `eligible` debe ser **operativa**, no solo informativa. Proveedores visibles pero no verificados tendrán estado/razón independiente, nunca autorización implícita.

## 4. Decisiones técnicas previas a escribir código

### 4.1 Admisión común

Extraer una función de decisión pura compartida por `plan`, `run` y `reserve`, con un resultado auditable (`eligible`, `reasons`, `provider`, `quoted_worst_units`, `account_id`, `profile`). Separar la elección local de la elección remota: `run --local` **solo** admite un candidato local, no el mejor proveedor remoto.

- `plan`: lectura, explicación y selección; no side effects.
- `run --local --approve`: resolución del plan y revalidación de proveedor local justo antes de ejecutar.
- `reserve`: revalidación **atómica** de presupuesto contra ledger bajo lock; nunca dispara trabajos remotos.
- Identidad de cuenta: evitar que dos cuentas distintas llamadas `github` se contabilicen como una. Para v0.1.1, usar IDs únicos del catálogo por cuenta (con documentación de esa restricción); posponer estructura multi-cuenta explícita a un esquema posterior.

### 4.2 Contabilidad: estado por ejecución, no overwrite por evento

Diseñar y probar estados `unseen -> reserved -> settled|cancelled` y observaciones externas vinculadas a `(provider_account_id, run_ref)`.

**Regla conservadora v0.1.1:** para una misma ejecución, el consumo protegido equivale al **máximo** entre mediciones y liquidaciones no revocadas; nunca escoger silenciosamente el último valor (el bug `8 -> 3`). Si dos mediciones incompatibles requieren reducir el valor, **dejar pendiente la corrección explícita y auditada**, sin añadir ajustes silenciosos en esta versión.

Casos:
- `reserve=10; settle=8; observed=3` => uso protegido 8, sin reserva activa.
- `reserve=10; observed=12; settle=8` => uso protegido 12, sin reserva activa.
- `observed=3; observed=3` => idempotente.
- `observed=3; observed=9` => conflicto o nueva corrección explícita; no mutación silenciosa.
- `settle=8; settle=8` => idempotente o rechazo inequívoco; nunca dos sumas.
- `cancel` tras ejecución comprobada => no ocultar consumo observado.
- `reserve=10` sin cierre después de cambiar de mes => aún bloquea 10.

Distinguir **fecha del evento**, **periodo de facturación** y **fecha de reserva**. Para la compatibilidad inicial, las liquidaciones existentes con `month` heredado se interpretan como el mismo periodo que la reserva. Observaciones sin `month` explícito se atribuyen a su fecha, salvo que se vinculen con una ejecución conocida y se defina una resolución unívoca. Nunca migrar/eliminar el JSONL original de forma destructiva.

### 4.3 Seguridad de almacenamiento y procesos

- Mantener `fcntl.flock` y `fsync`, proteger permisos `0600` del libro/lock y directorio `0700` cuando se crea.
- Definir conducta ante registro incompleto, symlinks y concurrentes multiproceso.
- Usar grupo de procesos POSIX para timeouts: `start_new_session=True`, escalado de señal y espera/recolección; nunca enviar señales a un grupo ajeno.
- Limitar comandos a `argv[]` más `shell=False`; reconocer que un script de proyecto aprobado sigue siendo código de confianza del proyecto.
- No imprimir credenciales, tokens o valores de entornos por defecto.

### 4.4 Contratos y compatibilidad

- Conservar CLI y nombres del paquete existentes; cambiar solamente errores y rutas que sean inconsistentes.
- Mantener `.ci-broker/project.json` esquema v1 y leer el ledger JSONL anterior.
- El `init` debería escribir `["python", "--version"]` como ejemplo **o** usar un selector portable documentado; las pruebas han de impedir reintroducir `sys.executable` absoluto.
- Prohibir un fallback implícito si el usuario indicó `CI_BROKER_ACCOUNTS`.
- No comparar valores crudos de créditos, USD, minutos o vCPU-min entre proveedores. La clasificación usa fracción de presupuesto **del mismo tipo de unidad**.

## 5. Paquetes de trabajo (orden cerrado)

| Paquete | Tareas | Dependencias | Evidencia obligatoria |
|---|---|---|---|
| **P0 — Congelar contrato y reproducir** | Convertir los 4 defectos confirmados + proveedor no verificado en tests de regresión; añadir fixtures aislados de cuentas/ledger, test de comando que deja marca y caso de mes | Ninguna | Tests nuevos inicialmente fallan en el baseline con causa prevista; no modifican estado real |
| **P1 — Unificar admisión** | Extraer evaluación común; garantizar `run --local` y `reserve` conforme a plan; distinguir no-verificado; robustecer comprobación de capacidades y duración | P0 | INV-01, 02, 03, 04; plan y ejecución producen decisiones consistentes |
| **P2 — Reparar ledger** | Definir/probar máquina de estados, procedencia, máximos de consumo, idempotencia, periodos, reservas y revalidación con lock | P0; contrato de P1 | INV-05 a 09; pruebas multiproceso y retrospectivas pasan |
| **P3 — Portabilidad y seguridad** | Arreglar `init` y lectura de cuentas; validación estricta; timeout por grupo; JSONL/symlinks/permisos; plugins y grafo | P1-P2 | INV-10 a 16; no quedan procesos hijos ni fallbacks silenciosos |
| **P4 — Compatibilidad y documentación** | Ajustar README, ARCHITECTURE, LLM_CONTEXT, AGENTS y ejemplos Python/Rust/Go/Node/Extracta; documentar límites reales | P1-P3 | Ejemplos JSON válidos, comandos copiados funcionan cuando herramientas del proyecto están disponibles |
| **P5 — Verificación de cierre** | Ejecutar matriz local y revisión estática final; emitir informe con SHA, evidencias, límites y estado | P0-P4 | Todos los gates abajo; 0 cambios necesarios a Extracta o `main` |

No iniciar un paquete posterior por inercia si el anterior tiene una falla de contrato. Las correcciones del mismo defecto se agrupan; no abrir una iteración por cada fallo de un test.

## 6. Matriz de pruebas: cobertura por comportamiento

| Grupo | Escenarios mínimos |
|---|---|
| **Admisión** | local habilitado/deshabilitado, capacidades ausentes, `--approve` presente/ausente, timeout fuera de capacidad, proveedor remoto no verificado, ausencia de ejecutor local |
| **DAG** | nodo aislado, diamante con dependencia compartida, orden determinista, ciclo, nodo faltante, fallo temprano, perfil inexistente |
| **Presupuesto** | redondeo por proveedor, cuota exacta, margen 10%, cuota casi agotada, muchas reservas competidoras, cambio de mes, unidades inválidas |
| **Eventos** | reserve/settle/cancel/observed, registros repetidos, conflicto, consumo 8→3, consumo 3→8, cierre tardío, referencia usada por proyectos distintos |
| **Archivos** | JSON inválido, accounts override ausente, symlink, ledger truncado, permisos, escritura concurrente, lectura bajo lock |
| **Procesos** | comando argv inocuo, error de retorno, timeout, hijo persistente, cancelación de grupo, ningún subproceso al fallar admisión |
| **Portabilidad** | `init` sin rutas absolutas, invocación con `--project-root` desde otra carpeta, Python 3.15.2, aislamiento de estado, esquema v1 |
| **Arquitectura** | no imports de consumidores, plugins explícitos, cero red, cero dispatch, cero imports dinámicos desde JSON |

Preferir **tests de propiedad deterministas** para secuencias del ledger y pruebas **multiproceso reales**, no solo hilos. Cada defecto confirmado debe tener al menos un test rojo antes de su corrección.

## 7. Validación local, sin créditos de CI

Usar un entorno temporal o venv aislada, con `CI_BROKER_ACCOUNTS` y `CI_BROKER_LEDGER` apuntando a rutas controladas. Evitar ejecutar el workflow `.github/workflows/tests.yml` mientras no haya saldo.

```bash
# Desde la raíz del repositorio, con Python 3.15.2 ya instalado.
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall -q src tests
ci-broker --help

# Sin perder exit codes al registrar la salida:
set -o pipefail
python -m unittest discover -s tests -v 2>&1 | tee /tmp/ci-broker-unittest.log
echo "exit=${PIPESTATUS[0]}"
```

La validación completa requiere además CLI black-box contra dos proyectos temporales, pruebas de carrera de reservas usando procesos, fallos intencionales de configuración y verificación de procesos descendientes tras timeout.

**Versiones:** Python 3.15.2 será la principal (auditada por el usuario). Conservar compatibilidad declarada `>=3.11` y probar versiones disponibles localmente (por ejemplo 3.13), sin instalar herramientas adicionales salvo necesidad real; si una versión no puede probarse, marcarla `BLOCKED`, no `PASS`.

## 8. Reglas de cierre / Definition of Done

**PASS** solo si:
1. INV-01 a INV-16 tienen comprobaciones automatizadas con resultados reales.
2. No hay errores de unit tests, compileall ni smoke tests de CLI en el intérprete principal.
3. Tests multiproceso y cruces de mes no liberan presupuesto de forma indebida.
4. Ninguna regresión recrea los cuatro defectos auditados o la selección de proveedor remoto no verificado.
5. No existen cambios a `main` ni a Extracta; no se gastan minutos de CI externos.
6. La documentación no anuncia funcionalidades que siguen en el roadmap.
7. El informe final incluye SHA del commit, versiones ejecutadas, comandos exactos, resultado y limitaciones.

**FAIL** cuando alguna invariante queda violada. **BLOCKED** cuando falta una capacidad o evidencia de ejecución necesaria. Una suite verde sin las pruebas de las invariantes no es suficiente.

## 9. Seguimiento mínimo y riesgos de alcance

- Una tabla única: `P0..P5`, `PENDING / IN_PROGRESS / PASS / FAIL / BLOCKED`, SHA y evidencia.
- No repetir tests completos después de cambios puramente documentales; ejecutar tests focalizados por módulo y la matriz completa **solo en P5** (salvo cambios en interfaces globales).
- No crear nuevos proveedores ni dashboards, API web, schedulers paralelos o MCP en este cierre.
- Si una corrección exige romper el esquema v1 o el histórico JSONL, **detener ese cambio y documentar migración** antes de implementarlo.

### Riesgos que deben constar en el informe, aunque no se implementen ahora

1. El ledger JSONL con `fcntl` protege procesos del **mismo host**, no máquinas distintas.
2. La aprobación de comandos locales no constituye sandbox de scripts potencialmente maliciosos.
3. No existe prueba de compatibilidad real con servicios de CI de terceros hasta ejecutar un job en una cuenta real.
4. No hay optimización matemática avanzada ni ejecución paralela del grafo: ambos son roadmap independiente del cierre v0.1.1.

## 10. Entrega prevista

- Código de núcleo estabilizado (sin dependencia de consumidores).
- Tests de regresión y de invariantes, offline y aislados.
- README y guía LLM acordes con la realidad del paquete.
- Evidencia local reproducible en Python 3.15.2.
- Informe `docs/INFORME_VALIDACION_0_1_1.md` con decisión de aceptación.
- **Sin merge, release ni activación de CI remoto en este plan.**

## 11. Ruta posterior a la estabilización (no autoriza implementación)

Para conservar el alcance de la visión de **CI Broker como vendor/plugin agnóstico**, estos hitos quedan ordenados pero **NO integrados en P0–P5**:

| Horizonte | Objetivo | Gate para empezar |
|---|---|---|
| **v0.2 — SDK agnóstico** | Interfaces estables para `ProjectDetector`, `TaskRunner`, `ProviderClient` y `PipelineRenderer`; descubrimiento opcional de runtimes y perfiles sin obligar a que todos los proyectos sean Python. | v0.1.1 PASS, schema y CLI estables, pruebas en al menos dos proyectos de lenguajes distintos |
| **v0.3 — Grafo y optimizador** | DAG granular de tareas y pruebas, historial por firma, selección por impacto/cambio, detección de ejecuciones equivalentes, packing de memoria/CPU y planificación de paralelismo. Mantener explicaciones del porqué se ejecuta o se omite cada prueba. | SDK agnóstico probado, métricas reales reproducibles y benchmarks antes/después |
| **v0.4 — Proveedores remotos** | Generación de YAML por proveedor, lectura de saldo real, despacho/poll/cancel, cuotas verificadas por cuenta, reserva→ejecución→conciliación, autenticación opt-in y evidencia del job. | Presupuestos invariantes probados, cuentas conectadas con autorización explícita y cero overage por defecto |
| **v0.5+ — Coordinación distribuida** | Backend transaccional para múltiples hosts, agentes voluntarios/servidores propios y políticas de asignación de recursos entre proyectos. | Necesidad real demostrada de uso multi-host y diseño de seguridad de identidades y estado |

Estas fases son **horizontes de producto**, no una invitación a iterar indefinidamente. Requieren una decisión separada para implementarse. La arquitectura v0.1.1 no debe cerrarse de una manera que impida esos contratos, pero tampoco anticiparlos prematuramente.

**Regla de priorización permanente:** seguridad de admisión y presupuesto > reproducibilidad y observabilidad > reutilización agnóstica > optimización > ampliación de proveedores.

