"""Large command that should be streamed directly to keyboard without storing data"""
from epomakercontroller.commands.EpomakerCommand import EpomakerCommand
from epomakercontroller.commands.reports.Report import Report


class EpomakerStreamCommand(EpomakerCommand):
    def __init__(self, callback, *args, **kwargs):
        self._callback = callback
        super().__init__(*args, **kwargs)

    def _insert_report(self, report: Report) -> None:
        raise NotImplementedError

    def __iter__(self):
        raise StopIteration
