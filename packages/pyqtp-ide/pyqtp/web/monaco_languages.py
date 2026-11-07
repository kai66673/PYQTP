from __future__ import annotations

# Соответствие расширений файлов Monaco language id
# (используется для подсветки и для регистрации LSP-провайдеров на JS-стороне;
# язык должен совпадать с languageId в реестре хаба lsp_registry).
LANGUAGES_REGISTRY = {
    '.py': 'python',
    '.pyi': 'python',
    # C/C++ семейство — единый Monaco-язык 'cpp' (диалект определяет clangd)
    '.c': 'cpp',
    '.h': 'cpp',
    '.hpp': 'cpp',
    '.cpp': 'cpp',
    '.cc': 'cpp',
    '.cxx': 'cpp',
    '.hxx': 'cpp',
}
