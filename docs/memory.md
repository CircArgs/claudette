# Semantic memory and search

Claudette maintains a local search index over GitHub issues and PRs.

## Backends

| Backend | Library | Description | Install |
|---|---|---|---|
| `dense` | [model2vec](https://github.com/MinishLab/model2vec) (potion-base-8M, ~8 MB) | Semantic similarity via embeddings | `pip install claudette[dense]` |
| `bm25` | [bm25s](https://github.com/xhluca/bm25s) | Fast keyword search, no download | `pip install claudette[bm25]` |
| `hybrid` | Both | Combined via reciprocal rank fusion | `pip install claudette[search]` |

Chosen during `claudette init`.

## CLI

```bash
claudette memory sync                           # index all issues/PRs
claudette memory search "auth bug" --state open  # semantic search
claudette memory status                          # index stats
claudette memory clear                           # wipe the index
```

## Storage

```
.claudette/memory/
├── index.db          # sqlite metadata
├── embeddings.npy    # numpy embedding matrix
└── keys.json         # key-to-index mapping
```

Memory is synced before each manager session launch so workers can search for related issues.
