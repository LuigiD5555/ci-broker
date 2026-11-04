# CI Broker Agent Gateway — arquitectura canónica

## Decisión

CI Broker 0.2.0 tiene como núcleo una API estable para agentes, sin acoplarla a ChatGPT ni CircleCI. La rama `main` representa esa arquitectura. El `main` original se conserva sin modificar en `legacy/original-main-20261104` (`a8ed1bb6199cddf7d9d9f841ff36528bf7bf0c2c`).

### Interfaces (puertos de entrada)

- `ci-broker` (gateway CLI canónico); `ci-broker legacy` y `ci-broker-legacy` permiten el comportamiento local histórico.
- MCP stdio: `ci_plan`, `ci_dispatch`, `ci_status`, `ci_cancel`, `ci_jobs`, `ci_artifacts`, `ci_logs`.
- GitHub Actions `agent-control.yml`: comentarios del OWNER en el issue #2, comandos exactos `/ci-broker capabilities` y `/ci-broker selftest`. No ejecuta CI remoto ni recibe tokens de CircleCI.
- Futuro: MCP remoto HTTPS autenticado y puente privado hacia proveedores.

### Puertos de proveedor

`ci_broker.gateway.RemoteProvider` declara `dispatch`, `status`, `jobs`, `artifacts`, `logs` y `cancel`. Las implementaciones se registran explícitamente con `register_provider` o entradas instaladas en `ci_broker.remote_providers` y cargadas únicamente con `--trust-provider`.

Solo `CircleCIProvider` está implementado. GitHub Actions, GitLab, Buildkite y Jenkins aún NO son clientes remotos implementados. El selector de costes continúa en el planificador/ledger 0.1.1 sin convertir unidades heterogéneas directamente.

### Semántica de evidencia

- `status`: observación remota vinculada a reserva y commit SHA; éxito CI nunca equivale a aceptación funcional.
- `jobs`: consulta trabajos de workflows y deniega resultados paginados incompletos.
- `artifacts`: valida pertenencia del job al pipeline y devuelve solamente nombres, no URLs firmadas ni bytes.
- `logs`: consulta los metadatos de pasos de API v1.1 solo para slugs compatibles; no descarga stdout ni URLs prefirmadas. Para proyectos GitHub App no compatibles, devuelve indisponibilidad explícita.
- La evidencia de Extracta (repositorio privado) nunca debe publicarse en logs de GitHub Actions de CI Broker (repositorio público).

### Validación y límites

El test de gateway no despacha un pipeline pago: usa clientes simulados. El hook de GitHub se valida también mediante una ejecución `issue_comment` del propietario sobre `main`. La ejecución real CircleCI, la reconciliación duradera entre workers, el canal privado de logs y la instalación de un MCP remoto en ChatGPT están pendientes.

## Siguiente evolución sin romper contratos

1. Conector privado con autorización independiente, control de presupuesto persistente e idempotencia entre ejecuciones.
2. Recuperación de logs privados con tamaños acotados, redacción, permisos por proyecto y control de URL firmada.
3. Adaptadores reales para GitHub Actions, GitLab, Buildkite y Jenkins con el mismo protocolo.
4. Verificación física de Z01/G5b de Extracta, sin modificar el estado de su Plan Maestro prematuramente.
