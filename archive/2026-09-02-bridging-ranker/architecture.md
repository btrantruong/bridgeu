

Full pipeline

```
                                    NEW POSTS INGESTED 
                                             │
                    ┌────────────────────────┴────────────────────────┐
                    │                        │                        │
                    ▼                        ▼                        ▼
         Interaction snapshots         Follower graph              Content
        (likes, reposts)                                      (texts, URLs)
                    │                        │                        │
                    ▼                        ▼                        | 
        Latent representation   structural embedding s                |
                                          zᶜ_u                        |
               │                             │                        │
               |                             | 
      ┌────────┴────────────┐                |               ┌────────┴───────────────────┐
      ▼                     ▼                |               ▼                            ▼
  Relevanc        Topical/engagement         |          Topic model           Harmful Response (HaR) model
      │                  Bridging            |               │                            |
      │                     |                |               │                            |
      │                     |                |               |                            |
      │                     |                ▼               |                            |
      │                     |        Structural briding      |                            |
      │                     | (supplement for sparse data)   |                            |
      │                     │                │               
      │                     └──────┬─────────┘               |                            |
      ▼                            ▼                         ▼                            ▼ 
Relevance Retriever         Bridging Retriever            Topic                      HaR score  
            │                      │                          │                           │ 
      top-K_relevance          top-K_bridge                   │                           │ 
            └───────────┬──────────┘                          │                           │   
       Candidate Union + Deduplication                        |                           |
                        ▼                                     |                           |
                  Candidate set                               |                           |
                        └────────────────┬────────────────────┘                           |
                                         ▼                                                |
                           Diversify topic in candidate set                               |
                                          |───────────────────────────────────────────────┘
                                          ▼
                                       Ranker
                                      recency
                                      low HaR
                                     bridging
                                          │
                                          ▼
                                  Final Top-K Feed
```

Full pipeline — follower graph from a separate NETWORK SNAPSHOT source

```
   NETWORK SNAPSHOT                         NEW POSTS INGESTED
        │                                          │
        ▼                     ┌────────────────────┴────────────────────┐
  Follower graph              │                                         │
        │                     ▼                                         ▼
        ▼            Interaction snapshots                           Content
 structural embedding    (likes, reposts)                        (texts, URLs)
       zᶜ_u                    │                                         │
        │                     ▼                           ┌─────────────┴────────────┐
        │            Latent representation                 ▼                          ▼
        │                     │                       Topic model      Harmful Response (HaR) model
        │            ┌────────┴────────┐                   │                          │
        │            ▼                 ▼                   │                          │
        │        Relevance     Topical/engagement          │                          │
        │            │            Bridging                 │                          │
        ▼            │                 │                    │                          │
 Structural bridging │                 │                    │                          │
 (supplement for     │                 │                    │                          │
  sparse data)       │                 │                    │                          │
        │            │                 │                    │                          │
        └──────┬─────────────┼─────────┘                    │                          │
               │             │                              │                          │
               ▼             ▼                              ▼                          ▼
      Bridging Retriever   Relevance Retriever            Topic                    HaR score
               │                  │                         │                          │
          top-K_bridge       top-K_relevance                │                          │
               └────────┬─────────┘                         │                          │
        Candidate Union + Deduplication                     │                          │
                        ▼                                   │                          │
                  Candidate set                             │                          │
                        └───────────────┬───────────────────┘                          │
                                        ▼                                              │
                       Diversify topic in candidate set                                │
                                        │─────────────────────────────────────────────-┘
                                        ▼
                                     Ranker
                                    recency
                                    low HaR
                                   bridging
                                        │
                                        ▼
                                Final Top-K Feed
```

<!-- 2026-09-04: Editable stakeholder .pptx of the revised diagram added under 2026-09-04-pipeline-pptx/ -->
Editable slide: [2026-09-04-pipeline-pptx/bridging_ranker_pipeline.pptx](2026-09-04-pipeline-pptx/bridging_ranker_pipeline.pptx)