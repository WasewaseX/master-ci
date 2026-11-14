"""Report exporters (fixture: legitimate framework hook shape)."""


class Exporter:
    def __init__(self, registry):
        self.registry = registry

    def export(self, report):
        raise NotImplementedError


class LedgerExporter(Exporter):
    def export(self, report):
        rows = self.registry.rows(report)
        return "\n".join(f"{name}: {value}" for name, value in rows)
