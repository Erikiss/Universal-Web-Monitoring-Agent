Plan:
1. Create `lab_pubs_filter.py` that queries arXiv API for recent papers in `cs.*` and `stat.ML`.
2. Apply regexes mapping to the 51 labs on titles, abstracts, and author affiliations.
3. Group results by lab.
4. Output to `reports/labs_{today}.md`.
5. Maintain `seen_labs.json` so papers aren't reported multiple times.
6. Add `.github/workflows/lab-pubs-filter.yml` analogous to the lesswrong workflow.
