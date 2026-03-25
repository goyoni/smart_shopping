# How Price Search works
## What is Price Search?
Price Search is a tool that allows you to search for the price of one or multiple product models.
It searches the web and specific relevant websites to find the best price for the product model given, and then returns the results including the product description, prices and sellers information.
If searching for multiple models, it will return the best price for each model and try to find the best price for the bundle of models.

## How does it work?
### Phase 1: Search for the best price for each product model
The first phase is to find the best price for each product. This is done by breaking the search query into multiple products or models and searching for each separately.

The web_search_mpc should initiate one or multiple searches based on the search query. The search query is broken into multiple products or models and each is searched separately. The search results are then filtered  and deduped to remove irrelevant results and passed on the the web_scraper_mcp for further processing.

Search is several ways using:
1. regular web search
2. specific aggregators that index sellers search (like zap.co.il)
3. Aggregators who handle transactions search (e.g amazon)

Example:

For a fridge model M1, the mpc should:
1. search the web and find all relevant results
2. search websites like zap and get results (deduping results already found in 1)
3. search amazon for results (that are shipped to country of seller)

#### Search must be done in user local. If I'm in IL I'd expect the agent to figure out best sellers and aggregator sites and search there (bestbuy for example is irrelevant in IL)


### Phase 2: Scrape the price and seller information from the search results
web_scraper_mcp gets a list of URLs from the web_search_mpc and scrapes the price and seller information from each URL. It then returns a list of product models with their prices and sellers information.

#### Scraping strategies
web_scraper_mcp finds best price and seller information by trying different scraping strategies based on the html structure. Once finding one that works, it will store the strategy for future use. The way to validate a scraping strategy is to compare the results to expected results. e.g a price should make sense, a seller name should be a valid seller name, etc.

#### Caching
Since scraping is expensive, the web_scraper_mcp will cache the results for a certain period of time to avoid scraping the same URL multiple times.
For seller information, the web_scraper_mcp will scrape the seller name, seller URL, and seller contact info including whatsapp and email (those are the most important ones for the user). This data is stored in the cache for 90 days based on seller domain.
For price information, the web_scraper_mcp will scrape the price and currency. This data is not cached.

### Phase 3: Find the best price for the bundle of models
After recieving the results from the web_scraper_mcp, the web_search_mpc will return the results to the results_processor_mcp who will validate and process the results. The results_processor_mcp will then create a list of all sellers and their prices for each product model. For each seller, it will issue additonal searches to find prices of tthe remaining missing models in the users original query for a given seller.

Once done, it will format the results and return to the user.

###
Example Flow:
1. User queries for: m1, m2 (query q1)
2. 2 web searches (s1, s2) are triggered in parallel (one per model)
3. each web search returns a list of URLs for the respective model.
    Example:
        for s1: sr1, sr2, sr3... sr10
        for s2: sr2, sr3, sr4... sr10
    etc.
4. scraper goes through the list of URLs (in parrallel) for each model and scrapes the price and seller information.
    Example:
        for m1:
            seller1.com, 2000NIS
            seller2.com, 2050NIS
            seller3.com, 2000NIS
            etc.
        for m2:
            seller1.com, 1500NIS
            seller4.com, 2000NIS
            seller5.com, 19000NIS
            etc.
        etc.
5. the results are sent to the results_processor_mcp which creates a map of all sellers and their prices for each product model.
    Example (structure can be different):
        {
            "seller1.com": {
                "models": {"m1": "2000NIs", "m2": "1500NIS"}
            },
            "seller2.com": {
                "models": {"m1": "2050NIs", "m2": NULL}
            }
            "seller3.com": {
                "models": {"m1": "2000NIs", "m2": NULL}
            }
            "seller4.com": {
                "models": {"m1": NULL, "m2": "2000NIS"}
            }
            "seller5.com": {
                "models": {"m1": NULL, "m2": "1900NIS"}
            }
        }
6. for each seller with a missing product price, issue a dedicated query at that sellers website.
    Example:
        queries for m1 in seller4.com and seller5.com will be triggered, results:
            seller4.com and seller5.com find nothing
        queries for m2 in seller2.com and seller3.com will be triggered, results:
            seller2.com has m2 at 1950NIS,  seller3.com find nothing
7. All results are aggregated and returned to user
    Example:
        Bulk Sellers:
        Seller1
            m1: 2000NIS, link to model page
            m2: 1500NIS, link to model page
            total: 3500NIS
            contact info + link to seller
        Seller2
            m1: 1950NIS, link to model page
            m2: 2050NIS, link to model page
            total: 4000NIS
            contact info + link to seller

        Non aggregated results
        m1 (description, image):
            seller1: price & link
            seller2, price & link
            seller3: price & link
        m2 (description, image):
            seller1: price & link
            seller2, price & link
            seller4: price & link
            seller5: price & link


## Sanity:
Since the flow is complex, we will create an e2e / evaluation test that runs a real search expecting to get the following results:
    query: BFL523MB1F, HBG578EB3, PVS631HC1E, SMV4HAX21E
    expected results:
    at least 2 aggregate containing https://www.soferavi.co.il/ and https://www.superelectric.co.il/
10 unique non aggregated results per model.
