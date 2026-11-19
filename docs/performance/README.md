# Performance data

`results.json` holds the numbers plotted in the
[Performance by GPU](../../README.md#performance-by-gpu) charts, measured with
the v1.5.0 images and the whole stack on one GPU machine. Streaming latency is
the median time from the end of a spoken turn to its final transcript, with every
stream sending continuous dense speech in real time. Sync latency is the median
time to transcribe a one-minute request of dense speech, plotted against the
one-minute requests completed per second while a fixed number of requests were
kept in flight. To redraw the SVGs after editing `results.json`, run this from
the repository root with matplotlib installed:

```bash
python3 docs/performance/make_charts.py
```
