# all functions related to user accounts

import hashlib

from models import *

from base64 import b64decode, b64encode
import json
import requests
from search_helpers import get_article_object

GET = requests.get


async def create_pmid(handler, user, repo_name, pmid, github_token):
    """ Create a file for this PMID. """
    p = int(pmid)
    pmid_data = {
        "message": "Add {0}.json".format(p),
        "content": encode_for_github(
            {})}
    await handler.github_request(requests.put,
                                 "repos/{0}/{1}/contents/{2}.json".format(
                                     user,
                                     repo_name,
                                     p),
                                 github_token,
                                 pmid_data)
    return True


async def get_or_create_pmid(handler, user, collection_name, pmid, github_token):
    """ Get the contents of this PMID file, or create it if it doesn't
    exist. """
    p = int(pmid)
    repo_name = get_repo_name_from_collection(collection_name)
    try:
        pmid_contents = await handler.github_request(
            GET, "repos/{0}/{1}/contents/{2}.json".format(
                user, repo_name, p), github_token)
        return pmid_contents
    except OSError as e:
        # The article didn't already exist
        await create_pmid(handler, user, repo_name, p, github_token)
        pmid_contents = await handler.github_request(
            requests.get, "repos/{0}/{1}/contents/{2}.json".format(
                user, repo_name, p), github_token)
        return pmid_contents


def encode_for_github(d):
    """ Encode with base64 and utf-8. Take a dictionary. """
    return b64encode(
        json.dumps(
            d,
            indent=4,
            sort_keys=True).encode("utf-8")).decode("utf-8")


def decode_from_github(s):
    """ Reverse the encode_dict_for_github function.
    Return a dictionary. """
    return json.loads(b64decode(s).decode("utf-8"))


def get_repo_name_from_collection(name):
    """ Convert a collection name to a repository name for GitHub. """
    return "brainspell-neo-collection-" + name.replace(" ", "-")


def get_collection_from_repo_name(name):
    """ Convert a repo name to the user-specified collection name. """
    return name[len("brainspell-neo-collection-"):]


def get_github_username_from_api_key(api_key):
    """ Fetch the GitHub username corresponding to a given API key. """

    user = User.select().where((User.password == api_key))
    user_obj = next(user.execute())
    return user_obj.username


def valid_api_key(api_key):
    """ Return whether an API key exists in our database. """

    user = User.select().where((User.password == api_key))
    return user.execute().count >= 1


def get_user_object_from_api_key(api_key):
    """ Return a PeeWee user object from an API key. """

    return User.select().where(User.password == api_key).execute()


def register_github_user(user_dict):
    """ Add a GitHub user to our database. """

    # if the user doesn't already exist
    if (User.select().where(User.username ==
                            user_dict["login"]).execute().count == 0):
        username = user_dict["login"]
        email = user_dict["email"]
        hasher = hashlib.sha1()
        # password (a.k.a. API key) is a hash of the Github ID
        hasher.update(str(user_dict["id"]).encode('utf-8'))
        password = hasher.hexdigest()
        User.create(username=username, emailaddress=email, password=password)
        return True
    else:
        return False  # user already exists


def add_collection_to_brainspell_database(
        collection_name,
        description,
        api_key,
        cold_run=True):
    """ Create a collection in our database if it doesn't exist,
    or return false if the collection already exists. """

    if valid_api_key(api_key):
        user = list(get_user_object_from_api_key(api_key))[0]

        # get the dict of user collections
        if not user.collections:
            user_collections = {}
        else:
            # unfortunately, because malformatted JSON exists in our database,
            # we have to use eval instead of using JSON.decode()
            user_collections = eval(user.collections)

        # if the collection doesn't already exist
        if collection_name not in user_collections:
            # create the collection
            user_collections[collection_name] = {}
            user_collections[collection_name]["description"] = str(description)
            user_collections[collection_name]["pmids"] = []
            if not cold_run:
                q = User.update(
                    collections=json.dumps(user_collections)).where(
                    User.username == user.username)
                q.execute()
            return True
    return False


def bulk_add_articles_to_brainspell_database_collection(
        collection, pmids, api_key, cold_run=True):
    """ Adds the PMIDs to collection_name, if such a collection exists under
    the given user. Assumes that the collection exists. Does not add repeats.

    Takes collection_name *without* "brainspell-collection".

    Return False if an assumption is violated, True otherwise. """

    user = get_user_object_from_api_key(api_key)
    if user.count > 0:
        user = list(user)[0]
        if user.collections:
            # assumes collections are well-formed JSON
            target = json.loads(user.collections)
            if collection not in target:
                target[collection] = {
                    "description": "None",
                    "pmids": []
                }

            pmid_set = set(map(lambda x: str(x), target[collection]["pmids"]))
            for pmid in pmids:
                pmid_set.add(str(pmid))

            target[collection]["pmids"] = list(pmid_set)
            if not cold_run:
                q = User.update(
                    collections=json.dumps(target)).where(
                    User.password == api_key)
                q.execute()
            return True
        else:
            return False  # user has no collections; violates assumptions
    return False  # user does not exist


def remove_all_brainspell_database_collections(api_key):
    """ Dangerous! Drops all of a user's Brainspell collections
    from our local database. Does not affect GitHub.

    Called from CollectionsEndpointHandler."""

    if valid_api_key(api_key):
        q = User.update(
            collections=json.dumps({})).where(
            User.password == api_key)
        q.execute()


def get_brainspell_collections_from_api_key(api_key):
    """
    Return a user's collections from Brainspell's database given an API key.

    May be inconsistent with GitHub.
    """

    response = {}
    if valid_api_key(api_key):
        user = list(get_user_object_from_api_key(api_key))[0]
        if user.collections:
            return json.loads(user.collections)
    return response


def add_article_to_brainspell_database_collection(
        collection, pmid, api_key, cold_run=True):
    """
    Add a collection to our local database. Do not add to GitHub in this function.

    Assumes that the collection already exists. Assumes that the user exists.

    Takes collection_name *without* "brainspell-collection".
    Returns False if the article is already in the collection, or if an assumption
    is violated.

    This is an O(N) operation with respect to the collection size.
    If you're adding many articles, it's O(N^2). If you're adding multiple articles,
    please use bulk_add_articles_to_brainspell_database_collection.

    Called by AddToCollectionEndpointHandler.
    """

    user = get_user_object_from_api_key(api_key)
    if user.count > 0:
        user = list(user)[0]
        if user.collections:
            # assumes collections are well-formed JSON
            target = json.loads(user.collections)
            if collection not in target:
                target[collection] = {
                    "description": "None",
                    "pmids": []
                }
            pmids_list = set(
                map(lambda x: str(x), target[collection]["pmids"]))
            # provide a check for if the PMID is already in the collection
            if str(pmid) not in pmids_list:
                pmids_list.add(str(pmid))
                target[collection]["pmids"] = list(pmids_list)
                if not cold_run:
                    q = User.update(
                        collections=json.dumps(target)).where(
                        User.password == api_key)
                    q.execute()
                return True
            else:
                return False  # article already in collection
        else:
            return False  # user has no collections; violates assumptions
    return False  # user does not exist


def remove_article_from_brainspell_database_collection(
        collection, pmid, api_key, cold_run=True):
    """ Remove an article from the Brainspell repo. Do not affect GitHub.

    Takes collection_name *without* "brainspell-collection".

    Similar implementation to add_article_to_brainspell_database_collection. """

    user = get_user_object_from_api_key(api_key)
    if user.count == 0:
        return False

    user = list(user)[0]
    if not user.collections:
        return False

    # assumes collections are well-formed JSON
    target = json.loads(user.collections)
    if collection not in target:
        return False

    pmids_list = list(
        map(lambda x: str(x), target[collection]["pmids"]))
    if str(pmid) not in pmids_list:
        return False

    pmids_list.remove(str(pmid))
    target[collection]["pmids"] = pmids_list
    if not cold_run:
        q = User.update(
            collections=json.dumps(target)).where(
            User.password == api_key)
        q.execute()
    return True


def cache_user_collections(api_key, collections_obj):
    """ Force overwrite the existing user collection field with
    the passed collections_object data.  """
    q = User.update(
        collections=json.dumps(collections_obj)).where(
            User.password == api_key)
    q.execute()


def add_unmapped_article_to_cached_collections(api_key, pmid, collection_name):
    query = list(
        User.select(
            User.collections).where(
            User.password == api_key).execute())[0]
    collections = json.loads(query.collections)
    relevant_article = list(get_article_object(pmid))[0]
    target_collection = [
        x for x in collections if x['name'] == collection_name][0]
    target_collection['unmapped_articles'].append({
        'title': relevant_article.title,
        'pmid': relevant_article.pmid,
        'authors': relevant_article.authors,
        'reference': relevant_article.reference,
    })
    cache_user_collections(api_key, collections)
