"""Quick open (этап 9.2): fuzzy-скоринг и скан кэша файлов проекта.

Модуль не завязан на GUI — обе части покрываются юнит-тестами без QApplication.
"""

from __future__ import annotations

import os
from pathlib import Path

# Границы «слов» в путях: совпадение сразу после них получает бонус
# (в т.ч. «/» как граница каталогов в относительных путях).
_SEPARATORS = frozenset('/._- ')

# Бонусы скоринга (фиксируются юнит-тестами).
_BONUS_START = 8       # совпадение с самого начала цели
_BONUS_BOUNDARY = 8    # старт query сразу после разделителя
_BONUS_CONSECUTIVE = 6  # предыдущий символ query совпал с предыдущим символом цели
_BASE_MATCH = 1        # базовый вес каждого совпавшего символа

# Каталоги, которые не попадают ни в дерево проекта, ни в кэш quick open
# (имена с точкой отсекаются всегда).
_PRUNE_DIRS = frozenset({'__pycache__', 'node_modules'})


def fuzzy_score(query: str, target: str) -> int | None:
    """Оценка соответствия ``query`` строке ``target`` (``None`` — не подпоследовательность).

    Регистронезависимое вхождение; оптимальное выравнивание считается
    динамическим программированием (dp[i][j] — лучший скор для
    ``query[:j+1]``, заканчивающийся ровно на ``target[i]``), поэтому жадный
    скан не «съедает» бонусы подряд/границ. Бонус границы даётся первому
    символу query: совпадение, начавшееся с границы «слова», важнее разрыва
    цепочки «подряд».
    """
    if not query:
        return 0
    q = query.lower()
    t = target.lower()
    n = len(q)
    if n > len(t):
        return None

    # best_any[j] — максимум dp[i'][j] для пройденных i' < текущего i.
    best_any = [-1] * n
    # dp[j] для предыдущей позиции цели (для бонуса «подряд»).
    dp_prev = [-1] * n
    for i, ch in enumerate(t):
        dp_cur = [-1] * n
        boundary = i > 0 and t[i - 1] in _SEPARATORS
        for j in range(min(i, n - 1), -1, -1):
            if q[j] != ch:
                continue
            if j == 0:
                if i == 0:
                    score = _BASE_MATCH + _BONUS_START
                else:
                    score = _BASE_MATCH + (_BONUS_BOUNDARY if boundary else 0)
            else:
                # выбор позиции предыдущего совпадения: ровно i-1 (цепочка)
                # или любая более ранняя (максимум best_any). Граница на j>0
                # не бонусится: иначе «deep.py» с «p» после точки обгоняла бы
                # «dp.py» с цепочкой подряд (фиксирует юнит-тест)
                chain = dp_prev[j - 1]
                if chain >= 0:
                    chain += _BONUS_CONSECUTIVE
                base = max(chain, best_any[j - 1])
                if base < 0:
                    continue
                score = base + _BASE_MATCH
            dp_cur[j] = max(dp_cur[j], score)
        best_any = [max(b, c) for b, c in zip(best_any, dp_cur, strict=False)]
        dp_prev = dp_cur

    final = best_any[-1]
    return final if final >= 0 else None


def iter_project_files(root: Path) -> list[str]:
    """Относительные пути всех файлов проекта (через ``/``, отсортированы).

    Отсекаются точечные каталоги/файлы и служебные ``_PRUNE_DIRS`` — тот же
    набор, что скрывает дерево проекта. Синхронный обход: вызывается из
    фонового потока контрола.
    """
    files: list[str] = []
    for dir_path, dir_names, file_names in os.walk(root, topdown=True):
        dir_names[:] = [
            d for d in dir_names if not d.startswith('.') and d not in _PRUNE_DIRS
        ]
        for name in file_names:
            if name.startswith('.'):
                continue
            rel = Path(dir_path, name).relative_to(root)
            files.append(str(rel).replace(os.sep, '/'))
    files.sort()
    return files
