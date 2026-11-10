"""Диагностики LSP как «проблемы» (этап 10.1): разбор и агрегация.

Чистый модуль (без GUI и без Qt): превращает параметры нотификации
``textDocument/publishDiagnostics`` в набор ``Problem`` и даёт хелперы
агрегации (счётчики/подписи серьёзности) для панели «Проблемы» и
статус-бара.

Координаты: вход — LSP (0-based, символы UTF-16), выход — Monaco-стиль
(1-based, та же схема, что у перехода ``DocumentManager.open_definition_uri``
и у парсера поиска этапа 9.3). Смещение ``lsp_header`` не учитывается: в
живом IDE ``set_lsp_header`` не вызывается (проверено), ``headerLines`` в
JS-стеке всегда 0.

Серьёзность — коды LSP: 1=Ошибка, 2=Предупреждение, 3=Информация,
4=Подсказка (отсутствие ``severity`` по спецификации = 1). ``owner`` — имя
LSP-сервера-источника: basedpyright и ruff публикуют в один uri независимо,
поэтому в сторе хаба и в агрегате проблемы живут слитно (как маркеры Monaco
по разным ``owner``).

Разбор толерантный: мусорные записи пропускаем, а не падаем — нотификация
приходит из чужого процесса.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

SEVERITY_ERROR = 1
SEVERITY_WARNING = 2
SEVERITY_INFORMATION = 3
SEVERITY_HINT = 4

# порядок вывода категорий в подписях (ошибки впереди)
SEVERITY_ORDER = (SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFORMATION,
                  SEVERITY_HINT)

SEVERITY_LABELS: dict[int, str] = {
    SEVERITY_ERROR: 'Ошибка',
    SEVERITY_WARNING: 'Предупреждение',
    SEVERITY_INFORMATION: 'Информация',
    SEVERITY_HINT: 'Подсказка',
}

# порядок для подписей во множественном числе («Ошибок: 2»)
SEVERITY_COUNT_LABELS: dict[int, str] = {
    SEVERITY_ERROR: 'Ошибок',
    SEVERITY_WARNING: 'Предупреждений',
    SEVERITY_INFORMATION: 'Информации',
    SEVERITY_HINT: 'Подсказок',
}


@dataclass(frozen=True, order=True)
class Problem:
    """Одна проблема: координаты Monaco (1-based) + seriousness-поля.

    Порядок полей = порядок сортировки (строка, колонка, затем остальные).
    """

    line: int
    col: int
    end_line: int
    end_col: int
    severity: int
    message: str
    code: str = ''
    source: str = ''
    owner: str = ''


def severity_label(severity: int) -> str:
    """Русская подпись серьёзности; неизвестное значение — «Ошибка»."""
    return SEVERITY_LABELS.get(severity, SEVERITY_LABELS[SEVERITY_ERROR])


def file_path_from_uri(uri: str) -> str | None:
    """Локальный путь для ``file://`` uri; ``None`` — не-файловый/битый uri."""
    parsed = urlparse(uri)
    if parsed.scheme != 'file' or not parsed.path:
        return None
    return unquote(parsed.path)


def diagnostics_from_publish(
    params: object, owner: str
) -> tuple[str, list[Problem]]:
    """Разбор параметров ``textDocument/publishDiagnostics``.

    Возвращает ``(uri, проблемы)``; для пустого/не-файлового uri —
    ``('', [])`` (такую публикацию в стор записывать не нужно). Пустой
    список при валидном uri — штатная «очистка» проблем файла.
    """
    if not isinstance(params, dict):
        return '', []
    uri = params.get('uri')
    if not isinstance(uri, str) or file_path_from_uri(uri) is None:
        return '', []
    raw_items = params.get('diagnostics')
    if not isinstance(raw_items, list):
        return uri, []
    problems = [
        problem
        for problem in (_problem_from_diagnostic(entry, owner)
                        for entry in raw_items)
        if problem is not None
    ]
    problems.sort()
    return uri, problems


def severity_counts(problems: Iterable[Problem]) -> dict[int, int]:
    """Счётчик проблем по серьёзности (только фактические значения)."""
    return dict(Counter(problem.severity for problem in problems))


def summarize_severities(problems: Iterable[Problem]) -> str:
    """Подпись набора: «Ошибок: 2, Предупреждений: 1» (без нулей, '' — пусто)."""
    counts = severity_counts(problems)
    return ', '.join(
        f'{SEVERITY_COUNT_LABELS[severity]}: {counts[severity]}'
        for severity in SEVERITY_ORDER
        if counts.get(severity)
    )


def problem_label(problem: Problem) -> str:
    """Строка проблемы для дерева/журнала: «12:5 Ошибка: текст (код)»."""
    label = f'{problem.line}:{problem.col} {severity_label(problem.severity)}: {problem.message}'
    if problem.code:
        return f'{label} ({problem.code})'
    if problem.source:
        return f'{label} [{problem.source}]'
    return label


# --- F8-цикл по проблемам (этап 10.2) ---

# сплющенная точка проблемы для навигации: (путь файла, строка, колонка);
# порядок сравнения — путь, затем строка/колонка (тот же порядок, что у
# дерева панели: файлы по пути, внутри файла — по координатам)
ProblemEntry = tuple[str, int, int]


def next_problem(
    entries: Sequence[ProblemEntry],
    current: ProblemEntry | None,
    *,
    forward: bool = True,
) -> ProblemEntry | None:
    """Следующая/предыдущая проблема в списке по каретке (циклически).

    ``entries`` — отсортированный список точек (путь, строка, колонка);
    ``current`` — положение каретки ( ``(путь, строка, колонка)`` ) или None.
    Точного совпадения с ``current`` в списке может не быть (каретка между
    проблемами, файл без проблем) — выбирается первая точка строго после /
    перед ним в лексикографическом порядке. Пустой список — ``None``;
    ``current`` вне диапазона — с конца (цикл, как ``next_bookmark_line``).
    """
    if not entries:
        return None
    if current is None:
        return entries[0] if forward else entries[-1]
    if forward:
        for entry in entries:
            if entry > current:
                return entry
        return entries[0]
    before: ProblemEntry | None = None
    for index in range(len(entries) - 1, -1, -1):
        if entries[index] == current:
            # каретка точно на проблеме: назад — к предыдущей, не к себе
            return entries[index - 1] if index > 0 else entries[-1]
        if entries[index] < current:
            before = entries[index]
            break
    return before if before is not None else entries[-1]


# --- внутренности разбора ---


def _problem_from_diagnostic(entry: object, owner: str) -> Problem | None:
    """Одна запись ``Diagnostic`` -> ``Problem``; мусор — ``None``."""
    if not isinstance(entry, dict):
        return None
    message = entry.get('message')
    if not (isinstance(message, str) and message):
        return None
    coord = entry.get('range')
    if not isinstance(coord, dict):
        return None
    raw_start = coord.get('start')
    raw_end = coord.get('end')
    start = raw_start if isinstance(raw_start, dict) else {}
    end = raw_end if isinstance(raw_end, dict) else {}
    raw_source = entry.get('source')
    return Problem(
        line=_coord(start.get('line')),
        col=_coord(start.get('character')),
        end_line=_coord(end.get('line')),
        end_col=_coord(end.get('character')),
        severity=_severity(entry.get('severity')),
        message=message,
        code=_code(entry.get('code')),
        source=raw_source if isinstance(raw_source, str) else '',
        owner=owner,
    )


def _coord(value: object) -> int:
    """LSP 0-based координата -> Monaco 1-based; мусор/отрицательное -> 1."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 1
    return value + 1


def _severity(value: object) -> int:
    """Код серьёзности LSP (1..4); отсутствие/мусор/неизвестное — Ошибка."""
    if isinstance(value, bool) or not isinstance(value, int):
        return SEVERITY_ERROR
    return value if value in SEVERITY_LABELS else SEVERITY_ERROR


def _code(value: object) -> str:
    """Код диагностики (``string | integer`` по спецификации) -> строка."""
    if isinstance(value, bool):
        return ''
    if isinstance(value, int):
        return str(value)
    return value if isinstance(value, str) else ''
