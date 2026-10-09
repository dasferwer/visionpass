# Независимая синтетическая CV-оценка

Исследовательский прогон 09.10.2026 проверяет настоящий `FaceRecognitionEncoder`
(OpenCV decoding, dlib detection/128D encoding) и тот же Euclidean distance/порог
`distance <= 0.60`, что использует сервис. Это парная проверка заявленной личности,
а не измерение HTTP API, поиска ближайшего среди всего мероприятия, очереди,
ручного решения или защиты от spoofing/liveness. Stub здесь отклоняется.

## Источник и право использования

Официальный [Microsoft DigiFace-1M](https://github.com/microsoft/DigiFace1M),
README/лицензия ревизии `1d173f305de9afefafb8589ce8e726ec685863e5`.
Bae et al., *DigiFace-1M: 1 Million Digital Face Images for Face Recognition*,
WACV 2023. Это внешние синтетические изображения с identity labels, а не фотографии
реальных участников. Источник признаёт возможное непреднамеренное сходство с людьми.

[Полный R-UDA v1.0](DigiFace-LICENSE.txt) допускает только non-commercial computational
research. Ограничение касается **данных и результатов**: их нельзя использовать
в коммерческом предложении, для улучшения продукта/сервиса для других людей.
Результат здесь — исследовательское свидетельство границ готового matcher,
не разрешение применять эти данные или результат коммерчески. Изображения и
эмбеддинги не включены в репозиторий; лицензия исследовательского набора не
заменяется лицензией кода проекта. При отдельной передаче изображений потребовалось
бы сохранить attribution и связать получателя R-UDA (§3).

## Замороженный протокол

До первого вызова CV выбраны личности 0..11 и рендеры 0, 1, 18 из официальной
части `subjects_0-1999_72_imgs.zip`. Рендер 0 — enrollment, 1/18 — probes.
Microsoft описывает 4 набора аксессуаров по 18 ракурсов на личность;
разбиение использует исходные identity directories, никаких искусственных labels.
Нет train/calibration split, обучения или настройки порога: все 12 внешних
личностей целиком held-out для этого проекта. Пересечение с обучением готовой
предобученной dlib модели независимо не проверялось. Sequential selection не
является случайной репрезентативной выборкой. Отказы не заменялись удачными лицами.

[manifest.json](manifest.json) фиксирует URL, размер/ETag ZIP, каждое изображение
SHA256 и выборку. Downloader требует тот же ETag, HTTP 206, bounded reads,
проверяет размер/CRC ZIP; evaluator повторно проверяет SHA256 до encoding.
Полный 2.94 GB архив не скачивался, его SHA256 не заявляется. Сжатая central
directory и отдельные selected entries получены через Range.

Для каждой пары claimed identity/probe identity есть два probe trials:
24 genuine, 264 impostor; всего 288. Одна enrollment/probe ошибка делает trial
непринимаемым. FAR/FRR по всем попыткам включают отказ enrollment/detection;
encoded-only показатели используют только пары с двумя векторами. Нет minimum
success gate и нет подбора порога по результатам. Wilson 95% — описательный
биномиальный интервал, **не** гарантия популяционного покрытия: повторные картинки
и личности делают trials зависимыми. Малый N=12 и synthetic-to-real domain gap
не позволяют объявлять эти величины FAR/FRR реальной популяции или SLA.

## Фактический результат

[Полный результат и trials](result.json), Python 3.12.13, macOS arm64,
face-recognition 1.3.0, models 0.3.0, dlib 20.0.1, OpenCV 4.14.0.94.

| Показатель | Ошибки / знаменатель | Доля | Wilson 95%, описательный |
|---|---:|---:|---:|
| Отказ image encoding | 13 / 36 | 36.11% | 22.48–52.42% |
| Отказ enrollment | 5 / 12 | 41.67% | 19.33–68.05% |
| Trial без двух embeddings | 176 / 288 | 61.11% | 55.37–66.56% |
| FAR всех impostor attempts | 12 / 264 | 4.55% | 2.62–7.78% |
| FRR всех genuine attempts | 14 / 24 | 58.33% | 38.83–75.53% |
| FAR encoded-only | 12 / 101 | 11.88% | 6.93–19.63% |
| FRR encoded-only | 1 / 11 | 9.09% | 1.62–37.74% |

При исходном пороге matcher плохо переносится на эту небольшую выборку;
низкий FAR по всем attempts отчасти вызван большим числом отказов детектора.
Результат не доказывает достаточное качество контроля доступа на реальных людях.

## Воспроизведение

Выполнять только в рамках non-commercial research по R-UDA. Выходной каталог
должен находиться вне репозитория; он содержит только синтетические изображения.

```sh
uv sync --frozen --extra dev --extra cv
python scripts/download_digiface.py /tmp/visionpass-digiface /tmp/digiface-manifest.json
# Сравнить frozen SHA256 с docs/cv-evaluation/manifest.json; evaluator проверяет их.
uv run --extra cv python scripts/evaluate_cv.py /tmp/visionpass-digiface \
  --manifest docs/cv-evaluation/manifest.json --output /tmp/cv-result.json
uv run --extra dev pytest --noconftest scripts/test_cv_evaluation.py
```

Если ETag изменился, downloader завершится ошибкой; новый источник требует
отдельного протокола, не перезаписывания исторических результатов. Extra `cv`
содержит `setuptools<81`, поскольку models 0.3.0 использует удаляемый
`pkg_resources`; совместимость действительно проверена в этом прогоне.
Unit-тесты metrics используют искусственные vectors только для проверки
знаменателей/ошибок входа и отдельно от реального CV измерения.
