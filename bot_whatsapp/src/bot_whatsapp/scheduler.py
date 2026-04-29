import os
import logging
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import handlers

log = logging.getLogger(__name__)
TZ = os.environ.get("TZ", "America/Argentina/Buenos_Aires")


def _encuesta_lunes() -> None:
    # Lunes 14:00 → partido el miércoles 21hs
    fecha = date.today() + timedelta(days=2)
    log.info("Scheduler: enviando encuesta para el %s", fecha)
    handlers.send_encuesta(fecha, horario="21")


def _encuesta_viernes() -> None:
    # Viernes 14:00 → partido el domingo 20hs
    fecha = date.today() + timedelta(days=2)
    log.info("Scheduler: enviando encuesta para el %s", fecha)
    handlers.send_encuesta(fecha, horario="20")


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=TZ)
    scheduler.add_job(_encuesta_lunes, CronTrigger(day_of_week="mon", hour=14, minute=0, timezone=TZ))
    scheduler.add_job(_encuesta_viernes, CronTrigger(day_of_week="fri", hour=14, minute=0, timezone=TZ))
    scheduler.start()
    log.info("Scheduler iniciado (L/V 14:00 %s)", TZ)
    return scheduler
