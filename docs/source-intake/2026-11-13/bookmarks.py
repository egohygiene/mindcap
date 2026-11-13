#!/usr/bin/env python3
import os
import json
from datetime import datetime, timedelta

# Path to Chrome's Bookmarks file (adjust for your OS)
chrome_bookmarks_file = os.path.expanduser("~/Library/Application Support/Google/Chrome/Default/Bookmarks")

# Load the Bookmarks JSON file
with open(chrome_bookmarks_file, "r") as f:
    bookmarks_data = json.load(f)

# Get the current timestamp and calculate the timestamp for X days ago (e.g., 7 days)
days_ago = 7
cutoff_time = datetime.now() - timedelta(days=days_ago)

# Chrome's bookmark timestamps are in microseconds since January 1, 1601
# Calculate the cutoff timestamp in Chrome's reference time (1601-01-01)
chrome_epoch_start = datetime(1601, 1, 1)
cutoff_timestamp = int((cutoff_time - chrome_epoch_start).total_seconds() * 1000000)

# Function to recursively search for bookmarks created within the last X days
def find_recent_bookmarks(bookmark_node, recent_bookmarks):
    if "children" in bookmark_node:  # Traverse folder structure
        for child in bookmark_node["children"]:
            find_recent_bookmarks(child, recent_bookmarks)
    else:
        if bookmark_node.get("date_added"):
            date_added = int(bookmark_node["date_added"])  # date_added is in microseconds
            if date_added >= cutoff_timestamp:
                recent_bookmarks.append(bookmark_node["url"])

# List to store recent bookmarks
recent_bookmarks = []

# Start from the root of the bookmarks tree
find_recent_bookmarks(bookmarks_data["roots"]["bookmark_bar"], recent_bookmarks)
find_recent_bookmarks(bookmarks_data["roots"]["other"], recent_bookmarks)

# Deduplicate the bookmarks
deduplicated_bookmarks = list(set(recent_bookmarks))

# Save the deduplicated bookmarks to a text file
with open("chrome_recent_bookmarks.txt", "w") as f:
    for url in deduplicated_bookmarks:
        f.write(url + "\n")

print("Recent Chrome bookmarks saved and deduplicated in 'chrome_recent_bookmarks.txt'.")
