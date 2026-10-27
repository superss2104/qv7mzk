# Demo video outline (under 10 minutes)

Keep the recording under five minutes for the customer pitch and five minutes for the technical walkthrough. Replace the deployment placeholder after the public app is live.

## Product pitch (about 4:30)

**0:00–0:35 — Problem**  
“Wind output is uncertain, and buyers make decisions using forecasts that change through the day. Comparing a forecast with the latest estimate after the fact can overstate what was knowable at decision time. This desk keeps the forecast publication time visible and lets us evaluate only information that was available at the chosen cutoff.”

**0:35–1:10 — Scope**  
Introduce the Jan 15, 2026 example as a single-day diagnostic. State the sources: Elexon WINDFOR forecast vintages and FUELHH realized wind generation. Clarify that this is an accuracy view, not an imbalance-cost calculator.

**1:10–2:00 — Decision rule**  
Show the delivery date and selection-rule controls. Choose the 07:30 UTC fixed cutoff. Explain that a forecast published after 07:30 cannot be selected, even if it is closer to actuals.

**2:00–3:00 — Performance view**  
Walk through MAE, RMSE, signed error, matched coverage, and the forecast-versus-actual chart. Explain that positive signed error is overforecasting and the cost of either direction depends on imbalance prices and the buyer’s position.

**3:00–3:45 — Interval transparency**  
Open the hourly interval table. Point out delivery time, selected forecast, publication time, actual, and signed error. Explain that incomplete actual hours remain missing.

**3:45–4:30 — Takeaway**  
State the single-day diagnostic result and its limits. Invite users to change the cutoff and observe which vintage is selected. Close with the deployment URL once available.

## Technical walkthrough (about 4:45)

**0:00–0:45 — Architecture**  
Show the React/Vite frontend, FastAPI routes, testable Python services, normalized CSV snapshot, and Dockerfile. The API and built frontend share one origin in the container.

**0:45–1:45 — Source investigation**  
Show `investigation.md` and `scripts/ingest_data.py`. Explain why FUELHH uses settlement-date filters and WINDFOR uses publication-time filters. Show that WINDFOR `from`/`to` selected a current vintage for an old delivery window, so it was rejected for backtesting.

**1:45–2:45 — Preventing look-ahead**  
Open `forecast_selection.py`. Explain `publish_time <= cutoff`, fixed-cutoff and fixed-horizon modes, deterministic duplicate behavior, and explicit missing results.

**2:45–3:40 — Metrics and assumptions**  
Open `evaluation.py`. Explain that hourly actuals are the mean of two complete half-hour FUELHH records, coverage counts remain separate, and metrics use matched intervals only. Show the Jan 15 golden fixture and explain that the result is not a full January backtest.

**3:40–4:25 — Tests and local run**  
Run `python -m unittest discover -s tests -v`, then show the frontend build command and local URLs. Mention the API smoke check.

**4:25–4:45 — Submission status**  
Give the public GitHub URL, deployed URL, and note any known limitations. Do not claim deployment until those links resolve.
