"""Reused provisional profiles check the survey in the actual work directory."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from porter import pipeline


class SurveyReuse(unittest.TestCase):
    def check_layout(self, nested):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tid = '01004AD014BF0000'
            work, games = root / 'private', root / 'games'
            profile = work / 'profile' / tid
            profile.mkdir(parents=True)
            (profile / 'game.tsv').write_text('test')
            survey = (work if nested else games) / f'{tid}_survey'
            survey.mkdir(parents=True)
            port = object.__new__(pipeline.Port)
            port.work = work
            port.o = SimpleNamespace(folder=games)
            port.use_profile_dir = mock.Mock()
            with mock.patch.object(pipeline.nsp, 'require_64bit') as check, \
                 mock.patch.object(pipeline.game, 'load'), \
                 mock.patch.object(pipeline.android_profile, 'AndroidProfile'):
                port.experimental_profile(None, tid)
            check.assert_called_once_with(survey / 'extract/ExeFS', tid)
            self.assertTrue(port.provisional)

    def test_private_work_survey(self):
        self.check_layout(True)

    def test_legacy_survey(self):
        self.check_layout(False)
