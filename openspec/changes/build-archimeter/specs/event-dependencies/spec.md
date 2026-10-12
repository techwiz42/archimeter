## ADDED Requirements

### Requirement: Built-in presets
Presets SHALL ship for Redis pub/sub, Celery tasks, Django signals, blinker, Kafka producer/consumer topics and Node `EventEmitter`, enabled via config.

#### Scenario: Redis preset
- WHEN `redis` is enabled and code calls `r.publish("orders", ...)` and `p.subscribe("orders")`
- THEN event `orders` has one publisher and one subscriber

### Requirement: Custom patterns
Config SHALL define publish/subscribe call or decorator names and the argument position or keyword holding the event name.

#### Scenario: Project-specific bus
- WHEN config declares `bus.emit(name, ...)` as publish and `@bus.on(name)` as subscribe
- THEN those sites become event edges

### Requirement: Name resolution
Event names SHALL resolve from string literals, module-level constants (including imported) and enum members. Runtime-built names SHALL be reported as unresolved and MUST NOT be guessed.

#### Scenario: f-string
- WHEN the name is `f"user.{kind}"`
- THEN the site is listed under unresolved events with file and line

### Requirement: Cross-container matching
Events SHALL match across containers and services; a publisher in one service and subscriber in another SHALL yield a service-level edge.

#### Scenario: Cross-service
- WHEN service `api` publishes `job.done` and `worker` subscribes
- THEN a service edge `api -> worker` of kind event exists

### Requirement: Fixed-event patterns
A custom pattern SHALL be able to name one fixed event for a call that itself denotes that event.

#### Scenario: Entity helper
- WHEN a pattern has `event = "entity.created"` for `publish_entity_created`
- THEN every call to that helper is a publisher of `entity.created`
