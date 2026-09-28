"""Attach the explanatory caption to the slide as PowerPoint speaker notes."""
from pptx import Presentation

CAPTION = """\
Bridging Ranker — For-You Pipeline. The pipeline builds a user's feed in two \
phases: multi-source retrieval, which gathers a broad pool of candidate posts, \
and ranking, which orders that pool into the final feed.

a. Multi-source retrieval. Signals are drawn from two ingestion streams that \
run on independent schedules. A dedicated network snapshot provides the \
follower graph through a periodic crawl, while the new-posts stream provides \
interaction snapshots (likes, reposts) and post content (texts, URLs); keeping \
them separate lets the graph refresh on its own cadence rather than once per \
post. Each stream is converted into features: the follower graph into a \
structural embedding z^c_u for every user, the interaction snapshots into a \
latent collaborative-filtering representation of user preference, and the \
content into a topic model and a harm (HaR) model. Two retrievers then query \
this pool in parallel. The bridging retriever looks for posts that win \
approval across otherwise-separate audiences, scoring them from the structural \
embedding together with an engagement signal derived from the latent \
representation. The relevance retriever looks for posts that match the user's \
own tastes, scoring them from the latent representation alone. Their two \
top-K lists are merged, de-duplicated, and spread across topics using the \
topic model so that no single subject dominates the candidate set.

b. Ranking. The diversified candidate set is ordered by a single \
multi-objective score that rewards recent posts, penalises harmful ones using \
the HaR score routed directly from the harm model, and rewards bridging \
strength carried over from retrieval. The top of this ranking is returned as \
the final Top-K feed shown to the user.
"""

p = Presentation("bridging_ranker_pipeline.pptx")
p.slides[0].notes_slide.notes_text_frame.text = CAPTION
p.save("bridging_ranker_pipeline.pptx")
print("notes added")
