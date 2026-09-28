## Notes by Eurosky engineer 
API is basically the feed generator + internal search
Ingex is ingesting from graze megastream + jetstream and storing in DBs (mostly elasticsearch)
engagement-prediction is their model and they probably also store predictions on elasticsearch? I haven't fully parsed that repo
So my understanding so far is that we build a kind of Candidate generation of our own :
https://deepwiki.com/greenearth-social/api/4.4.2-candidate-generation-framework
We can host it on our infra perhaps and then query their internal APIs from that. I think we will still need our own version of engagement-prediction to provide additional signals, and we have to decide whether to feed that pipeline's data back into their DBs, or to host it locally, and our feed generator to query both their DB and ours internally

## Their question:
A feed generator in MySky is composed of :

Candidate Generators which query ElasticSearch and propose posts
Rankers which rank the combined outputs of candidate generators
A feed configuration which defines how to combine the above and user preferences. This is what configure the server for the actual feed


Decision 1 : Graph and Community Detection service
The SprinD project focuses on community-based signals which currently don't exist in MySky and this is probably where it makes sense to focus. Maintaining a follow-graph requires ingestion, and community detection is an algorithm of its own. In my view we have 2 options there:

Approach 1
External service on Eurosky infra - Aggregates follow graph from Jetstream and run community detection algorithm. Make available as an API for MySky or other Retrieval or Ranking
Pros:

Bonus Points for Europeanness
Faster headstart : We already have a similar service, which could be adapted for this
Cons:

Coordination cost of running services across orgs


Approach 2
Extend MySky's Ingest pipeline to add follow graph and community detection algorithm
Pros:

full utilization of MySky's framework and expertise
difficulty of sync between West Coast and Germany
Cons:

less of a european story, depending on the next decision


Decision(s) 2 : How much do we use Mysky's framework and where do we choose to integrate?
a. Are we using MySky as a feed generator? (Full pipeline on MySky side, except potentially graph/community detection) I think we don't actually have a choice here if we want to access any of the existing signals of MySky
b. Are we integrating at the ranking level
c. Are we integrating at the candidate generation
d. Are we planning to self-host a version MySky at some point? In which case integrating more makes sense