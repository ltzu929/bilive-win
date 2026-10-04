import logging
from bilive.config.secrets import redact


class SecretFilter(logging.Filter):
    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


scan_log = logging.getLogger("bilive.pipeline")
scan_log.addFilter(SecretFilter())
