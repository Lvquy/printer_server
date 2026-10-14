# Local appliance using CUPS, not an Odoo-specific web server

EDATEC is the local printer-management appliance; Odoo is only one client. CUPS owns device communication, drivers and spooling, while the application owns authenticated acceptance, idempotency, queue policy and tracking. This avoids reimplementing the mature print system or coupling hardware configuration to business applications. The initial deployment is one local device with SQLite; Internet/webhooks and multi-node coordination are deliberately out of scope.

## Consequences

Physical printing cannot be transactionally committed with SQLite. Persisted correlation/CUPS IDs are used for reconciliation; unresolved handoff outcomes require an explicit operator decision instead of an automatic reprint. A CUPS completed state is reported as such, not as universal proof of paper output.
