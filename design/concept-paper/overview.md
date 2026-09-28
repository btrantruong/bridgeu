# **BridgEU Feed Ranking Pipeline**

## **1\. Objective**

We aim to construct a For-You feed that is relevant and engaging while increasing exposure to content that generates positive engagement across otherwise separated audiences. To this end, the pipeline balances two objectives: personal relevance and bridging. Personal relevance captures whether a post is likely to appeal to a particular user. Bridging captures whether a post receives positive approval from a diverse audience [(Ovadya & Thorburn, 2022\)](https://www.zotero.org/google-docs/?8RChd8), particularly from users who historically approve of different content. 

The pipeline is a two-stage process, including candidate retrieval followed by ranking (Fig.1). A central design choice is to represent relevance and bridging as distinct retrieval objectives. Unlike previous approaches that introduce pro-social objectives only at the ranking stage [(Piccardi et al., 2025; Stray et al., 2026\)](https://www.zotero.org/google-docs/?js4b7A), our pipeline incorporates bridging into both retrieval and ranking. A dedicated retrieval pathway ensures that posts with demonstrated cross-audience approval can enter the candidate pool rather than relying solely on predicted engagement, after which the ranker balances relevance and bridging while accounting for negative interactions and discussion quality.

![][image1]

Fig.1. **BridgEU pipeline.** Signals are drawn from two data sources: the Network Snapshot provides the follower graph through a periodic crawl, while the New-posts Stream provides recent interaction snapshots such as likes, reposts, and post content, including texts, and URLs. These signals are transformed into content and user features that then become inputs to two phases of the recommendation pipeline. **a) Multi-source retrieval.** Two separate processes retrieve content that satisfy different objectives and pool them to create a Candidate set (red)**.**  Both retrieves use latent collaborative-filtering representation of posts and users obtained from interaction snapshots. a.1) The bridging retriever identifies posts receiving approval across otherwise separate audiences. Audience separation is measured along two dimensions: behavioral distance captures users who historically approve of different content, and network distance captures users who belong to different communities from the follower graph. Posts with strong cross-audience approval form the bridging Top-K. a.2) The relevance retriever identifies posts that match the user's tastes using the same latent preference space. This produces a separate relevance Top-K. Their two top-K lists are merged, de-duplicated, and spread across topics using the topic model so that no single subject dominates the candidate set. **b) Ranking**. The merged candidate set is scored according to multiple objectives. A Harmful Responses (HaR) model estimates the likelihood that a post will generate toxic or otherwise harmful responses. The final ranking rewards personally relevant and bridging content, favors recent posts, and penalizes posts with high predicted harmful-response risk. The highest-scoring posts are returned as the final For-You feed.

## **2\. Recommendation Pipeline**

### **2.1 Represent users and posts through interactions**

Recent positive interactions, such as likes and reposts, are used to estimate users’ preferences. Replies and other ambiguous interactions are treated separately because engagement may reflect agreement, disagreement, or antagonism rather than approval.

As an initial implementation, collaborative filtering factorizes the user-post interaction matrix into latent representations of users and posts. A user representation summarizes the kinds of posts that user has historically approved, while a post representation captures the audiences to which the post tends to appeal.

These representations support both retrieval objectives. For relevance retrieval, they provide a conventional estimate of how well a post matches a user’s preferences. For bridging retrieval, they provide a behavioral measure of distance between users: users are farther apart when they historically approve of different sets of posts. The follower graph provides a complementary measure of separation based on users’ positions or community memberships in the social network.

### **2.2 Multi-source candidate retrieval**

Retrieval consists of two separate processes designed to satisfy different objectives. The relevance retriever identifies posts likely to appeal to the target user, while the bridging retriever identifies posts that receive approval across otherwise separate audiences. Each produces its own Top-K list, which is subsequently pooled into a common candidate set.

#### 2.2.1 Retrieve candidates with diverse approval

This retrieval source operationalizes bridging. Conceptually, this source asks: Which posts are positively approved by people who normally approve of different things?

For each post, we identify its positively engaged audience and measure how different those users are according to their historical preference representations. A post receives a higher diverse-approval score when it attracts positive engagement from users whose historical preferences are farther apart.

This differs from identifying posts that geometrically lie between users in latent space. A post at the midpoint between two opposing preference profiles is not necessarily attractive to either audience. Diverse approval instead requires observed convergence: users with different historical preferences have actually responded positively to the same post.

Audience dispersion alone is insufficient. A post approved by two highly dissimilar users should not automatically outrank one receiving consistent cross-audience approval from hundreds of users. Conversely, highly popular posts may mechanically accumulate heterogeneous audiences. The score should therefore account for the amount of supporting evidence and relevant confounds, such as exposure, popularity, post age, and the diversity expected among posts with comparable reach.

The follower network provides a complementary representation of audience separation. Behavioral embeddings capture whether users tend to approve of different content, whereas network representations capture whether they occupy different regions of the social graph. These signals can initially remain separate and be combined only if empirical evidence supports doing so. The highest-scoring posts form the bridging candidate set.

#### 2.2.2 Retrieve candidates for personal relevance

This retrieval source implements the conventional recommendation objective: identify posts likely to appeal to the target user. Under collaborative filtering, posts can be retrieved according to their predicted user-post interaction score or their proximity to the user’s latent preference representation. The top-k posts form the relevance candidate set. This pathway ensures that the final feed remains responsive to individual preferences.

### **2.3 Construct the candidate set**

For each target user, the relevance and bridging candidate sets are merged into a common pool. The retrieval stage therefore provides two explicit pathways into the feed:

1. Personal relevance: candidates likely to appeal to this user.  
2. Diverse approval: candidates that have demonstrated positive appeal across otherwise different audiences.

A permissive relevance threshold can optionally be applied to bridging candidates to exclude clearly irrelevant content. This threshold should establish minimal compatibility rather than reproduce conventional relevance retrieval; otherwise, the bridging pathway would lose its ability to recover candidates missed by the relevance retriever.

### **2.4 Rank for relevance, bridging, and interaction quality**

The ranker scores the merged candidate set according to multiple objectives. Its goal is to produce a feed that remains personally relevant while promoting content with evidence of cross-audience approval and reducing the visibility of content likely to generate harmful interactions.

For each user-post pair, ranking features include predicted personal relevance, behavioral and network-based bridging signals, and recency. A Harmful Responses (HaR) model provides an additional content-based signal by estimating the likelihood that a post will generate toxic or otherwise harmful responses. This distinction is important because diverse engagement does not necessarily imply constructive engagement: a post may attract multiple communities because it provokes hostility or antagonistic discussion rather than shared approval.

The final ranking therefore rewards personally relevant and bridging content, favors recent posts, and penalizes posts with high predicted harmful-response risk. The bridging score used for ranking may be the same score used during retrieval or a richer version computed after the candidate pool has been reduced. Because ranking operates over a substantially smaller item set than retrieval, it can use richer or more computationally expensive behavioral, network, and content-derived features. The highest-scoring posts are returned as the final For-You feed.

## **3\. Why Introduce Bridging During Retrieval?**

A key distinction from many recommendation architectures is that bridging enters during candidate retrieval rather than only during re-ranking. A conventional architecture retrieves candidates according to predicted relevance or engagement and then modifies their ordering to incorporate diversity, bridging, or other objectives. Thus, the ranker can only promote items that survived retrieval. This creates a potential bottleneck when retrieval relies on collaborative engagement patterns. A bridging post may appeal to users similar to the target user while also attracting substantially different audiences. This heterogeneous engagement can weaken its conventional relevance signal, causing the post to be filtered out before a bridging-aware ranker can consider it.

We address this bottleneck with a separate retrieval pathway for posts with demonstrated cross-audience approval. This pathway complements rather than replaces relevance retrieval: collaborative filtering may already surface bridging posts that are highly relevant to the target user. The additional pathway ensures that bridging posts with weaker relevance signals still enter the candidate set.

The overlap between the two candidate sets is therefore an important empirical quantity. If standard retrieval already achieves high recall of bridging posts, the dedicated retriever may add little value. If the bridging pathway substantially increases their recall, this provides evidence for maintaining it.

A second distinction is that bridging is defined primarily through behavior rather than predefined ideological, demographic, or semantic categories. The system asks whether users who historically approve of different content converge positively on the same post. Network structure provides an additional measure of separation without requiring communities to be assigned substantive labels.

## **4\. Experimental and Revisable Architecture**

The architecture separates relatively stable conceptual objectives from provisional technical implementations. The stable distinctions are among personal relevance, diverse approval, negative interaction, and interaction quality. How each quantity is estimated should remain open to experimentation. We will revise the architecture as we accumulate empirical evidence. 

**Improvement of user latent representation.** Collaborative filtering provides a transparent starting point for learning preference structure, but a two-tower model with approximate nearest-neighbor retrieval may ultimately provide better scalability, latency, cold-start handling, or representational flexibility. Alternative graph representations may outperform follower-network embeddings, and different audience-dispersion measures may better distinguish meaningful bridging from generic popularity. Experiments may also show that topic-conditioned representations are necessary to distinguish cross-community approval on the relevant dimension from diversity arising for unrelated reasons.

**Experimental ranking.** Initial experiments can use an interpretable weighted combination of relevance, bridging, negative interaction, and discussion quality. Later evidence may motivate a learned ranker, nonlinear interactions among objectives, user-specific trade-offs, or explicit constraints on final feed composition.

**Incremental evaluation.** Offline tests can establish whether each component measures its intended construct and improves candidate recall. Replay and simulation can examine how alternative operationalizations change the candidate distribution. Online experiments can then assess engagement, diverse approval, negative interactions, discussion quality, and longer-term network outcomes. Individual components can therefore evolve while preserving the central architecture: retrieve for relevance, retrieve for demonstrated cross-audience approval, and rank the combined candidates according to the broader objectives of the feed.

## **5\. Evaluation Metrics**

Evaluation focuses on whether the algorithm improves not only engagement, but also the quality and composition of interaction. We measure outcomes at the comment, post/thread, and network levels.

**Deliberative quality.** We assess whether discussions are substantive and coherent on both comment- and thread-level. Base on previous work, we define deliberative quality of a conversation through whether the argument provides reasons or evidence, acknowledging competing considerations [(Fu & Ollagnier, 2026; Oswald, 2022\)](https://www.zotero.org/google-docs/?DRgRHK), and constructively engaging with others [(Seckin et al., 2026\)](https://www.zotero.org/google-docs/?G9wRec). Conversation coherence captures whether replies meaningfully respond to preceding comments. These metrics will be captured by both human annotation and scaled with natural-language processing models ([(Fu & Ollagnier, 2026; Oswald, 2022\)](https://www.zotero.org/google-docs/?7d1xX2)6). 

**Civility and toxicity.** We measure the prevalence and severity of toxic interactions. Of particular interest is whether cross-community interactions remain civil. Successful bridging should increase constructive interaction across communities without increasing toxicity or hostile exchanges. We will compare between state- of-the-art toxicity detection models and use the best model to capture this metric. The models include toxic-BERT [(Hanu & Unitary team, 2020](https://www.zotero.org/google-docs/?Er48ar)), (2) Perspective API ([Lees et al., 2022\)](https://www.zotero.org/google-docs/?vlsL4V), and (3) OpenAI omni-moderation. 

**Audience diversity and bridging.** We assess whether posts attract positive engagement from otherwise separated audiences. Measures include audience diversity, average distance among engagers, balance of engagement across communities, and the share of engagement originating outside the author’s community. Positive engagement and replies are evaluated separately to distinguish diverse approval from diverse attention or conflict. A key outcome is whether posts achieve both high audience diversity and high positive-response rates.

**Network-level polarization.** Mention and reply networks capture whether local interactions accumulate into broader changes in community structure. Outcomes include network assortativity, cross-community edge share, and modularity. Because greater cross-community interaction can also reflect conflict, these structural measures are evaluated alongside interaction quality. Quality-weighted networks can further distinguish constructive cross-community exchanges from toxic or hostile ones.

[*Fu, Y., & Ollagnier, A. (2026). A Theoretically Grounded Approach to Summarizing Conversation Dynamics for Forecasting the Derailment of Online Conversations. In M. Liakata, V. P. Moreira, J. Zhang, & D. Jurgens (Eds.), Proceedings of the 64th Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers) (pp. 5368–5384). Association for Computational Linguistics. https://doi.org/10.18653/v1/2026.acl-long.243*](https://www.zotero.org/google-docs/?6nCAu3)   
[*Hanu, L. & Unitary team. (2020). Detoxify.*](https://www.zotero.org/google-docs/?6nCAu3)   
[*Lees, A., Tran, V. Q., Tay, Y., Sorensen, J., Gupta, J., Metzler, D., & Vasserman, L. (2022). A new generation of perspective api: Efficient multilingual character-level transformers. Proceedings of the 28th ACM SIGKDD Conference on Knowledge Discovery and Data Mining, 3197–3207.*](https://www.zotero.org/google-docs/?6nCAu3)   
[*Oswald, L. (2022). Automating the analysis of online deliberation. A Comparison of Manual and Computational Measures Applied to Climate Change Discussions. SocArXiv Papers Preprint.*](https://www.zotero.org/google-docs/?6nCAu3)   
[*Ovadya, A., & Thorburn, L. (2022). Bridging-based ranking. Harvard Kennedy School Belfer Center for Science and International Affairs.*](https://www.zotero.org/google-docs/?6nCAu3)   
[*Piccardi, T., Saveski, M., Jia, C., Hancock, J., Tsai, J. L., & Bernstein, M. S. (2025). Reranking partisan animosity in algorithmic social media feeds alters affective polarization. Science, 390(6776), eadu5584.*](https://www.zotero.org/google-docs/?6nCAu3)   
[*Seckin, O. C., Truong, B. T., Flammini, A., & Menczer, F. (2026). Identifying Constructive Conflict in Online Discussions through Controversial yet Toxicity Resilient Posts. Proceedings of the International AAAI Conference on Web and Social Media, 20(1), 2079–2093. https://doi.org/10.1609/icwsm.v20i1.42738*](https://www.zotero.org/google-docs/?6nCAu3)   
[*Stray, J., Baker, I., Beknazar-Yuzbashev, G., Budak, C., Kamin, J., Rutherford, K., Stalinski, M., Acosta, T., Bail, C., Bernstein, M., Brandt, M., Bruckman, A., Chhabra, A., De, S., Duskin, K., Fish, S., Goldberg, B., Guess, A., Hadfield-Menell, D., … Zheng, S. (2026). The Prosocial Ranking Challenge: Reducing Polarization on Social Media without Sacrificing Engagement. https://arxiv.org/abs/2603.19626*](https://www.zotero.org/google-docs/?6nCAu3)   

