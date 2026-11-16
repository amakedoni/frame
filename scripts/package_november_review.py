"""Build separate offline review and blind handoff packages from the frozen local batch."""
from html import escape
import json
from pathlib import Path
import shutil
from zipfile import ZipFile, ZIP_DEFLATED

from build_november_review import WORK, OUT, ROOT, load_json

LABELS={'D00':'#49bfff','D10':'#46e894','D20':'#ffd04b','D40':'#ff6960'}


def image_card(row, hint):
    item=row['item_id']
    file=(ROOT/row['file_path']).relative_to(ROOT/'annotation/pilot/local').as_posix()
    nodes=[]
    for label,x,y,x2,y2 in hint['boxes']:
        nodes.append(f'<rect x="{x}" y="{y}" width="{x2-x:.5f}" height="{y2-y:.5f}" stroke="{LABELS[label]}" stroke-width="2" fill="none" vector-effect="non-scaling-stroke"/>')
    classes=' '.join(f'<span class="chip" style="--color:{LABELS[c]}">{c}</span>' for c in dict.fromkeys(b[0] for b in hint['boxes']))
    return f'''<article id="{item}">
    <header><h3>{item}</h3>{classes}{'<span class="flag">Проверить в первую очередь</span>' if hint['priority'] else ''}</header>
    <div class="photo"><img src="{file}" alt="Дорога {item}" width="{row['width']}" height="{row['height']}" loading="lazy">
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">{''.join(nodes)}</svg></div>
    <p>{escape(hint['reason'])}</p><a href="{file}" target="_blank" rel="noopener">Открыть снимок без рамок</a>
    </article>'''


def html_review(path):
    summary=load_json(OUT/'draft_summary.json')
    road=load_json(WORK/'road_manifest.json')
    boxes={row['item_id']:row for row in load_json(WORK/'road_suggestions.json')}
    text_tasks=load_json(WORK/'text_blind_tasks.json')
    text={row['item_id']:row for row in load_json(WORK/'text_suggestions.json')}
    img_html='\n'.join(image_card(row,boxes[row['item_id']]) for row in sorted(road,key=lambda r:(not boxes[r['item_id']]['priority'],r['item_id'])))
    text_html=[]
    for task in sorted(text_tasks,key=lambda t:(not text[t['data']['item_id']]['priority'],t['data']['item_id'])):
        data=task['data']
        hint=text[data['item_id']]
        priority='<span class="flag">Спорный случай</span>' if hint['priority'] else ''
        text_html.append(f"<article id='{escape(data['item_id'])}'><header><h3>{escape(data['item_id'])}</h3><span class='chip'>{hint['label']}</span>{priority}</header><blockquote>{escape(data['text'])}</blockquote><p>{escape(hint['reason'])}</p></article>")
    path.write_text(f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer">
<title>Курсовая · проверка предразметки</title><style>
*{{box-sizing:border-box}}:root{{color-scheme:light}}body{{margin:0;background:#f5f7fa;color:#1c2b3c;font:16px/1.55 system-ui,sans-serif}}
main{{max-width:1200px;margin:auto;padding:32px 22px 50px}}h1{{font-size:clamp(27px,4vw,42px);line-height:1.16;margin:.3em 0 .45em}}
h2{{margin:32px 0 12px}}h3{{font-size:15px;letter-spacing:.02em;margin:0}}a{{color:#105a93}}nav{{display:flex;gap:20px;flex-wrap:wrap;margin:18px 0 6px}}
.note{{border-left:4px solid #d58b2a;padding:12px 15px;background:#fff4df;margin:16px 0}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}
article{{min-width:0;background:#fff;border:1px solid #d9e2ec;border-radius:9px;padding:13px 15px 15px}}header{{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-bottom:8px}}
.photo{{position:relative;width:100%;max-width:590px;line-height:0}}.photo img{{display:block;width:100%;height:auto}}.photo svg{{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}}
.chip{{display:inline-block;border:1px solid #d2dde7;border-left:5px solid var(--color,#647e94);padding:0 6px;border-radius:4px;background:#f5f8fa;font-size:13px}}
.flag{{font-size:13px;color:#754000;background:#fff3d8;padding:1px 7px;border-radius:4px}}article p{{margin:9px 0 6px;font-size:14px}}
blockquote{{margin:6px 0;white-space:pre-wrap;overflow-wrap:anywhere;color:#182532}}footer{{margin-top:30px;color:#526477}}
@media(max-width:700px){{.grid{{grid-template-columns:1fr}}main{{padding:23px 13px}}}}
</style></head><body><main><small>Встреча 18 ноября 2026 · рабочий комплект</small><h1>Проверка предразметки</h1>
<p>{summary['text_items']} комментария и {summary['road_items']} фотографий. Выше в каждом списке — спорные случаи.</p>
<p class="note"><strong>Здесь видны готовые подсказки.</strong> Перед независимой разметкой этот обзор и проекты Review draft не открывайте. Метки текстов предложены Codex, рамки перенесены из исходной разметки EPDD.</p>
<nav><a href="#road">Фотографии</a><a href="#text">Комментарии</a><a href="http://127.0.0.1:8081/projects/8/data">Править рамки в Label Studio</a><a href="http://127.0.0.1:8081/projects/7/data">Проверить текст в Label Studio</a></nav>
<section id="road"><h2>Дорожные снимки</h2><p>Синий — D00, зелёный — D10, жёлтый — D20, красный — D40. Увеличь снимок и проверь класс, границы, пропуски и повторы. Исправления сохраняются в Label Studio, этот обзор для просмотра.</p><div class="grid">{img_html}</div></section>
<section id="text"><h2>Комментарии</h2><p>Явная реклама и самопродвижение — spam; обычное обсуждение — ham. Ссылки показаны как часть текста, переходить по ним не нужно.</p><div class="grid">{''.join(text_html)}</div></section>
<footer>Источники: <a href="https://huggingface.co/datasets/hridaydutta123/YT-100K">YT-100K</a> и <a href="https://doi.org/10.5281/zenodo.20454801">EPDD, Leonardo Zambrano</a>. Сведения об источниках и изменениях в манифестах. Черновик от 17.11.2026. Отправленных ответов людей этот комплект не содержит.</footer>
</main></body></html>''',encoding='utf-8')


def write_readme(root, text):
    (root/'НАЧНИТЕ_ЗДЕСЬ.md').write_text(text,encoding='utf-8')


def build_packages():
    a_root=OUT/'A-review'
    a_root.mkdir(parents=True,exist_ok=True)
    b_root=OUT/'B-blind'
    b_root.mkdir(parents=True,exist_ok=True)
    for root, mode in [(a_root,'review'),(b_root,'blind')]:
        for kind in ('text','road'):
            shutil.copyfile(WORK/f'{kind}_{mode}_tasks.json',root/f'{kind}_tasks.json')
            if mode=='review':
                manifest=load_json(WORK/f'{kind}_manifest.json')
                for row in manifest:
                    if 'file_path' in row:
                        row['file_path']=(ROOT/row['file_path']).relative_to(ROOT/'annotation/pilot/local').as_posix()
                (root/f'{kind}_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                shutil.copyfile(WORK/f'{kind}_suggestions.json',root/f'{kind}_suggestions.json')
        for name in ('youtube.xml','rdd.xml'):
            shutil.copyfile(ROOT/'annotation/label_studio'/name,root/name)
        shutil.copyfile(ROOT/'docs/annotation_codebook.md',root/'annotation_codebook.md')
        for row in load_json(WORK/'road_manifest.json'):
            photo=ROOT/row['file_path']
            relative=photo.relative_to(ROOT/'annotation/pilot/local')
            dest=root/relative
            dest.parent.mkdir(exist_ok=True,parents=True)
            shutil.copyfile(photo,dest)
        if mode=='review':
            report=(ROOT/'docs/november_review.md').read_text(encoding='utf8')
            (root/'november_review.md').write_text(report,encoding='utf-8')
            html_review(root/'review.html')
            write_readme(root,'# Черновик Александра\n\nОткрой `review.html` в браузере и `november_review.md` для порядка проверки. Это черновые подсказки, не независимые ответы. Для слепого прохода открой чистый проект и вернись сюда только после двух экспортов.\n')
        else:
            write_readme(root,'''# Независимая разметка без подсказок

На основном компьютере задачи уже импортированы в отдельный проект B. Здесь копия для другого компьютера. Одна и та же выборка используется у A и B.

1. Прочитай `annotation_codebook.md`.
2. Установи Label Studio 1.23.2 в отдельном Python-окружении, если инструмента нет. Из распакованной папки в PowerShell, с активным окружением:

   ```powershell
   $env:LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED = 'true'
   $env:LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT = (Get-Location).Path.Replace('\\', '/')
   label-studio start --internal-host 127.0.0.1 --port 8082
   ```

3. В двух пустых проектах установи `youtube.xml` и `rdd.xml` в Labeling Setup → Code. В дорожном проекте добавь источник Local Files с абсолютным путём к папке `images` внутри архива и не запускай автосинхронизацию. Импортируй `text_tasks.json` и `road_tasks.json` соответственно.
4. Разметь тот же набор ID, что и Александр, не сверяясь с его метками. Пиши активное время в секундах. Сохрани два JSON-экспорта. Подсказок, готовых ответов и паролей в этом архиве нет.

Исходные данные: YT-100K (DOI 10.57967/hf/3602) и Leonardo Zambrano, EPDD (DOI 10.5281/zenodo.20454801). EPDD использован по CC BY 4.0; фото переведены в RGB JPEG без метаданных.
''')
        payload=OUT/f'november-18-{root.name}.zip'
        with ZipFile(payload,'w',compression=ZIP_DEFLATED,compresslevel=4) as zipf:
            for f in sorted(root.rglob('*')):
                if f.is_file():
                    zipf.write(f,f.relative_to(root).as_posix())
        with ZipFile(payload) as zipf:
            assert zipf.testzip() is None
        print(f'{payload.name}: {payload.stat().st_size:,} bytes')


if __name__=='__main__':
    build_packages()
