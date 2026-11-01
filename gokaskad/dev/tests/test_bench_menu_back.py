"""М-1 стенда 3.3.7.13: SSH-меню «Проверить сервер» (``k3_host_bench``) — у вопроса ядра об отдаче до точек российских
провайдеров (варианты ядра только «1) Замерить 2) Не замерять») меню печатает свою строку «0) Назад», как у вопроса о
виде проверки; «0» возвращает в меню без проверки, «1» и «2» — как раньше."""

import json
import shutil
import subprocess
import unittest

from tests.test_3351_cmdinj import function

STUBS = r"""
t() { printf '%s' "$1"; }
tf() { local k=$1; shift; printf '%s' "$k:$*"; }
clear() { :; }
k3_term_esc() { cat; }
k3_gw_pause() { :; }
k3_bad_choice() { echo BAD; }
k3ctl() {
    case "$*" in
        *--json*) printf '%s' "$JS" ;;
        "host bench --dry") echo PRICE ;;
        *) echo "RUN:$*" ;;
    esac
}
CYAN="" NC="" RED=""
"""

DRY = {"pkgs": {"question": None},
       "upload_ru": {"text": "UPLOAD?", "choices": [[1, "Замерить отдачу"], [2, "Не замерять"]], "pkgs": {"question": None}}}


def run(keys):
    src = function("k3_host_bench") + STUBS + "k3_host_bench\n"
    r = subprocess.run(["bash", "-c", src], input=keys, capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "LC_ALL": "C.UTF-8",
                            "JS": json.dumps(DRY)})
    return r.stdout + r.stderr


@unittest.skipUnless(shutil.which("jq"), "нет jq")
class UploadQuestionBackTest(unittest.TestCase):
    def test_the_upload_question_lists_back(self):
        out = run("2\n0\n")
        q = out[out.index("UPLOAD?"):]
        self.assertIn(" 1) Замерить отдачу\n 2) Не замерять\n 0) C_BACK\n", q)
        self.assertNotIn("RUN:", out)                       # «0» — назад, проверка не началась
        self.assertNotIn("BAD", out)

    def test_one_and_two_as_before(self):
        self.assertIn("RUN:host bench --full --yes --by-ssh\n", run("2\n1\n"))
        self.assertIn("RUN:host bench --full --yes --by-ssh --no-upload-ru\n", run("2\n2\n"))
        self.assertIn("RUN:host bench --full --yes --by-ssh\n", run("2\n\n"))   # Enter — «1» по умолчанию

    def test_quick_check_has_no_upload_question(self):
        out = run("1\n")
        self.assertNotIn("UPLOAD?", out)
        self.assertIn("RUN:host bench --quick --yes --by-ssh\n", out)


if __name__ == "__main__":
    unittest.main()
