# Разметка для Владимира

Распакуй архив `pilot-B-60x60.zip` в отдельную папку. В нём 60 комментариев и 60 дорожных фотографий с теми же `item_id`, что у Александра. Файлы `text_tasks.json` и `road_tasks.json` содержат задания. Правила разметки лежат в `annotation_codebook.md`, сведения об источниках — в файлах `*_manifest.json`.

Для участия с Codex открой в нём распакованную папку и вставь текст из `PROMPT.txt`. Сохрани `B_text_assistant.json` и `B_road_assistant.json` и передай их Александру. По уточнению условий разрешена разметка с Codex, если у каждого участника свой проход. Сохрани свою версию до просмотра разметки Александра.

## Как разметить самостоятельно в Label Studio

1. Создай два проекта, один для комментариев и один для снимков. В настройках разметки вставь соответственно `youtube.xml` и `rdd.xml`. Импортируй `text_tasks.json` в проект комментариев.
2. Для снимков при запуске Label Studio задай корнем локальных файлов распакованную папку. В дорожном проекте добавь Local Files с абсолютным путём к подпапке `images`. Импортируй `road_tasks.json` вручную, без автосинхронизации хранилища.
3. Пройди все 60 заданий каждого вида. Записывай фактическое время в секундах. Для спорных и непригодных примеров укажи причину. После каждого задания нажми **Submit**.
4. В обоих проектах выбери **Export → JSON**. Отправь Александру `B_text.json` и `B_road.json`.

Если нужен запуск на Windows, выполни из распакованной папки. Команда рассчитана на Python 3.11 и версию Label Studio из проекта.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r annotation-requirements.txt
$env:LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED = 'true'
$env:LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT = (Get-Location).Path.Replace('\', '/')
.\.venv\Scripts\label-studio.exe start --data-dir .\label-studio-b --internal-host 127.0.0.1 --port 8082 --no-browser
```

Открой в браузере `http://127.0.0.1:8082`.

## Как сопоставить два экспорта

Сопоставление идёт по `data.item_id`. Запусти из распакованной папки. Подставь путь к соответствующему файлу Александра, когда он его передаст.

```powershell
python compare_november_exports.py text A_text.json B_text.json text_tasks.json
python compare_november_exports.py road A_road.json B_road.json road_tasks.json
```

Для сравнения результатов с Codex используй флаг `--assistant`. После сохранения обеих версий можно взять файлы A из `annotation/november` в репозитории. Сверка использует контрольные суммы заданий для сопоставления опубликованных меток с архивом.

```powershell
python compare_november_exports.py text A_text_assistant.json B_text_assistant.json text_tasks.json --assistant
python compare_november_exports.py road A_road_assistant.json B_road_assistant.json road_tasks.json --assistant
```

Фотографии и тексты передаются отдельно от публичного репозитория. Источники и условия использования перечислены в манифестах архива.
