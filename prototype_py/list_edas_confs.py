import feedparser

feed_url = 'https://edas.info/rssConferences.php'
feed = feedparser.parse(feed_url)

for entry in feed.entries:
    print(f"{entry.title}: {entry.link}")
