#!/usr/bin/env python3

import os
import time
import json
import pathlib
import random
import requests
import datetime
import email.utils
import urllib.parse
from git import Repo
import leetcode_query

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
]


def get_headers(title_slug):
    return {
        "User-Agent": random.choice(USER_AGENTS),  # random from the list
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Referer": f"https://leetcode.com/problems/{title_slug}/",
        "Upgrade-Insecure-Requests": "1",
    }


def parse_git_log():
    commits = dict()
    for commit in Repo(os.getcwd()).iter_commits():
        if commit.message not in commits:
            commits[commit.message] = int(commit.committed_datetime.timestamp())

    return commits


def scrape_leetcode():
    print("[DEBUG] Starting scrape_leetcode()...", flush=True)

    # 1. check for environment variables
    leetcode_session = os.environ.get("LEETCODE_SESSION")
    csrf_token = os.environ.get("LEETCODE_CSRF_TOKEN")

    if not leetcode_session or not csrf_token:
        print("[ERROR] Missing LEETCODE_SESSION or LEETCODE_CSRF_TOKEN", flush=True)
        return []

    print(f"[DEBUG] Session token present (length: {len(leetcode_session)})", flush=True)
    print(f"[DEBUG] CSRF token present (length: {len(csrf_token)})", flush=True)

    # 2. create a session and set cookies
    session = requests.Session()
    session.cookies.set("LEETCODE_SESSION", leetcode_session, domain="leetcode.com")
    session.cookies.set("csrftoken", csrf_token, domain="leetcode.com")

    solved_problems = list()

    # 3. API request (with timeout)
    print("[DEBUG] Fetching problems from LeetCode API...", flush=True)
    try:
        all_problems = session.get("https://leetcode.com/api/problems/all/", timeout=30).json()
        print(f"[DEBUG] Successfully loaded problems. Total entries: {len(all_problems.get('stat_status_pairs', []))}",
              flush=True)
    except Exception as e:
        print(f"[ERROR] Failed to fetch problems: {e}", flush=True)
        return []

    # 4. Task list processing
    problem_counter = 0
    for problem in all_problems["stat_status_pairs"]:
        if problem["status"] == "ac":
            problem_counter += 1
            print(f"[DEBUG] Processing accepted problem #{problem_counter}...", flush=True)

            title_slug = problem["stat"]["question__title_slug"]
            headers = get_headers(title_slug)

            # Internal requests (with timeout)
            try:
                json_data = leetcode_query.question_detail
                json_data["variables"]["titleSlug"] = title_slug
                question_details = session.post("https://leetcode.com/graphql", json=json_data, headers=headers,
                                                timeout=30).json()

                json_data = leetcode_query.submission_list
                json_data["variables"]["questionSlug"] = title_slug
                submissions = session.post("https://leetcode.com/graphql", json=json_data, headers=headers,
                                           timeout=30).json()

                # check if there are submissions
                if not submissions.get("data", {}).get("questionSubmissionList", {}).get("submissions"):
                    print(f"[WARNING] No submissions found for {title_slug}. Skipping.", flush=True)
                    continue

                json_data = leetcode_query.submission_details
                json_data["variables"]["submissionId"] = \
                    submissions["data"]["questionSubmissionList"]["submissions"][0]["id"]
                submission_details = session.post("https://leetcode.com/graphql", json=json_data, headers=headers,
                                                  timeout=30).json()

                problem_info = {
                    "id": int(problem["stat"]["frontend_question_id"]),
                    "title": problem["stat"]["question__title"],
                    "title_slug": title_slug,
                    "content": question_details["data"]["question"]["content"],
                    "difficulty": question_details["data"]["question"]["difficulty"],
                    "skills": [tag["name"] for tag in question_details["data"]["question"]["topicTags"]],
                    "timestamp": int(submissions["data"]["questionSubmissionList"]["submissions"][0]["timestamp"]),
                    "language": submissions["data"]["questionSubmissionList"]["submissions"][0]["langName"],
                    "code": submission_details["data"]["submissionDetails"]["code"],
                }
                solved_problems.append(problem_info)

            except Exception as e:
                print(f"[ERROR] Failed processing {title_slug}: {e}", flush=True)
                continue

            time.sleep(1)  # leaving a pause between tasks

    print(f"[DEBUG] Finished processing. Total problems solved: {len(solved_problems)}", flush=True)
    return sorted(solved_problems, key=lambda entry: entry["timestamp"])


def update_readme(submissions):
    template = """
# LeetCode Submissions

> Auto-generated with [LeetCode Synchronizer](https://github.com/dos-m0nk3y/LeetCode-Synchronizer)

## Contents

| # | Title | Difficulty | Skills |
|---| ----- | ---------- | ------ |
"""

    for submission in submissions:
        title = f"[{submission['title']}](https://leetcode.com/problems/{submission['title_slug']})"
        skills = " ".join([f"`{skill}`" for skill in submission["skills"]])
        template += f"| {str(submission['id']).zfill(4)} | {title} | {submission['difficulty']} | {skills} |\n"

    with open("README.md", "wt") as fd:
        fd.write(template.strip())


def sync_github(commits, submissions):
    repo = Repo(os.getcwd())
    url = urllib.parse.urlparse(repo.remote("origin").url)
    url = url._replace(netloc=f"{os.environ.get('GITHUB_TOKEN')}@" + url.netloc)
    url = url._replace(path=url.path + ".git")
    repo.remote("origin").set_url(url.geturl())

    commit = list(repo.iter_commits())[0]
    repo.config_writer().set_value("user", "name", commit.author.name).release()
    repo.config_writer().set_value("user", "email", commit.author.email).release()

    for submission in submissions:
        commit_message = f"LeetCode Synchronization - {submission['title']} ({submission['language']})"
        if commit_message not in commits or commits[commit_message] < submission["timestamp"]:

            dir_name = f"{str(submission['id']).zfill(4)}-{submission['title_slug']}"

            language = submission["language"].lower().strip()

            if "c++" in language or "cpp" in language:
                ext = "cpp"
            elif "mysql" in language or "sql" in language:
                ext = "sql"
            elif "bash" in language:
                ext = "sh"
            elif "javascript" in language or "node.js" in language:
                ext = "js"
            elif "java" in language:
                ext = "java"
            elif "php" in language:
                ext = "php"
            elif "python" in language:
                ext = "py"
            elif "typescript" in language or "ts" in language:
                ext = "ts"
            else:
                if "c#" in language or "csharp" in language:
                    ext = "cs"
                elif "ruby" in language:
                    ext = "rb"
                elif "go" in language:
                    ext = "go"
                elif "rust" in language:
                    ext = "rs"
                elif "scala" in language:
                    ext = "scala"
                elif "kotlin" in language:
                    ext = "kt"
                elif "swift" in language:
                    ext = "swift"
                else:
                    # Remove spaces and special characters to create a secure extension
                    ext = "".join(c for c in language if c.isalnum())

            pathlib.Path(f"problems/{dir_name}").mkdir(parents=True, exist_ok=True)
            with open(f"problems/{dir_name}/{dir_name}.{ext}", "wt") as fd:
                fd.write(submission["code"].strip())
            with open(f"problems/{dir_name}/README.md", "wt") as fd:
                content = f"<h2>{submission['id']}. {submission['title']}</h2>\n\n"
                content += submission["content"].strip()
                fd.write(content)

            submission["skills"].sort()
            new_submission = {
                "id": submission["id"],
                "title": submission["title"],
                "title_slug": submission["title_slug"],
                "difficulty": submission["difficulty"],
                "skills": submission["skills"],
            }

            saved_submissions = list()
            if os.path.isfile("submissions.json"):
                with open("submissions.json", "rt") as fd:
                    saved_submissions = json.load(fd)

            if new_submission not in saved_submissions:
                saved_submissions.append(new_submission)
                saved_submissions = sorted(saved_submissions, key=lambda entry: entry["id"])
                update_readme(saved_submissions)
                with open("submissions.json", "wt") as fd:
                    json.dump(saved_submissions, fd, ensure_ascii=False, indent=2)

            # RFC 2822 (Thu, 07 Apr 2005 22:13:13 +0200) / ISO 8601 (2005-04-07T22:13:13)
            # https://github.com/gitpython-developers/GitPython/blob/master/git/objects/util.py#L134
            iso_datetime = email.utils.format_datetime(datetime.datetime.fromtimestamp(submission["timestamp"]))
            os.environ["GIT_AUTHOR_DATE"] = iso_datetime
            os.environ["GIT_COMMITTER_DATE"] = iso_datetime
            repo.index.add("problems/")
            repo.index.add("README.md")
            repo.index.add("submissions.json")
            repo.index.commit(commit_message)
            repo.git.push("origin")
            os.unsetenv("GIT_AUTHOR_DATE")
            os.unsetenv("GIT_COMMITTER_DATE")


def main():
    commits = parse_git_log()
    submissions = scrape_leetcode()
    sync_github(commits, submissions)


if __name__ == "__main__":
    main()
