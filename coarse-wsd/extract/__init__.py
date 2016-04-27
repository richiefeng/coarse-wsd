# -*- coding: utf-8 -*-

import wikipedia
import codecs
from bs4 import BeautifulSoup
import requests
from multiprocessing import Pool
import os
from wikipedia.exceptions import PageError, DisambiguationError
from collections import defaultdict as dd
from requests.exceptions import ConnectionError, ContentDecodingError
from time import sleep
from wikipedia.exceptions import WikipediaException


BASE_URL = u"https://en.wikipedia.org"
# What Links Here url. Redirection pages omitted.
WHAT_LINKS_HERE_URL = u"https://en.wikipedia.org/w/index.php?title=Special:WhatLinksHere/{}&limit={}&hideredirs=1"
MIN_SENTENCE_SIZE = 8

LOGGER = None

SLEEP_INTERVAL = 1


def extract_instances(content, word, pos, starting_instance_id, url=None):
    instances = []
    instances_replaced = []
    instances_all_replaced = []
    for line in content.split('\n'):
        tokens = line.split()
        num_of_tokens = len(tokens)
        if num_of_tokens >= MIN_SENTENCE_SIZE:
            sentence = []
            is_observed = False
            for i in xrange(num_of_tokens):
                if word in tokens[i].lower():
                    starting_instance_id += 1
                    instances.append(u"{} <{}.{}.{}>{}</{}.{}.{}> {}\t{}".format(u' '.join(tokens[:i]), word, pos, starting_instance_id,
                                                                                 tokens[i], word, pos, starting_instance_id,
                                                                                 u' '.join(tokens[i+1:]), url))

                    instances_replaced.append(u"{} <{}.{}.{}>{}</{}.{}.{}> {}\t{}".format(u' '.join(tokens[:i]), word, pos, starting_instance_id,
                                                                                word, word, pos, starting_instance_id,
                                                                                u' '.join(tokens[i+1:]), url))
                    sentence.append(u"<target>%s<target>" % word)
                    is_observed = True
                else:
                    sentence.append(tokens[i])

            if is_observed:
                instances_all_replaced.append(' '.join(sentence))

    return instances, instances_replaced, instances_all_replaced, len(instances)


def wiki_page_query(page_title, num_try=1):

    if num_try > 5:
        return None

    global SLEEP_INTERVAL

    try:
        LOGGER.debug(u'Retrieving {} from Wikipedia'.format(page_title.decode('utf-8')))
        p = wikipedia.page(page_title)
        SLEEP_INTERVAL = 1
        return p
    except PageError:
        LOGGER.debug(u"Page '{}' not found.".format(page_title.decode('utf-8')))
        # wikipedia library has a possible bug for underscored page titles.
        if '_' in page_title:
            title = page_title.replace('_', ' ')
            LOGGER.debug(u"Trying '{}'".format(title.decode('utf-8')))
            return wiki_page_query(title)
    # This is most likely the "What links here" page and we can safely skip it.
    except DisambiguationError:
        LOGGER.exception(u'Disambiguation Error for {}... get skipped.'.format(page_title.decode('utf-8')))
        return None
    except (ConnectionError, WikipediaException) as e:
        SLEEP_INTERVAL *= 2
        LOGGER.info(u"Sleeping {} seconds for {}. Reason: {}".format(SLEEP_INTERVAL, page_title.decode('utf-8'), e))
        sleep(SLEEP_INTERVAL)
        wiki_page_query(page_title)  # try again.
    except ContentDecodingError as e:
        LOGGER.info(u"{}... Trying ({})".format(e, num_try+1))
        wiki_page_query(page_title, num_try+1)


def extract_from_page(page_title, word, offset, fetch_links):
    pos = offset[-1]

    p = wiki_page_query(page_title)
    if p is None:
        LOGGER.warning(u'No page found for {}'.format(page_title.decode('utf-8')))
        return [], [], []

    instances, instances_replaced, instances_all_replaced, count = extract_instances(p.content, word, pos, 0, p.url)
    if fetch_links:
        links = fetch_what_links_here(p.title, limit=1000)
        for link in links:
            link_page_title = link.replace('/wiki/', '')
            # skip talk articles.
            if any(map(lambda x: link_page_title.startswith(x), ['Talk:', 'User_talk:', 'User:'])):
                continue
            link_page = wiki_page_query(link_page_title)
            if link_page is not None:
                num_try = 0
                content = None
                second_to_sleep = 10
                while num_try < 5 and content is None:
                    try:
                        content = link_page.content
                    except ConnectionError:
                        LOGGER.info(u"Content fetch error. {}".format(link_page_title.decode('utf-8')))
                        num_try += 1
                        second_to_sleep *= 2
                        sleep(second_to_sleep)
                link_instances, link_instances_replaced, link_instances_all_replaced, link_count = \
                    extract_instances(content, word, pos, len(instances), link_page.url)
                instances.extend(link_instances)
                instances_replaced.extend(link_instances_replaced)
                instances_all_replaced.extend(link_instances_all_replaced)

    return instances, instances_replaced, instances_all_replaced


def write2file(filename, lines):
    with codecs.open(filename, 'w', encoding='utf8') as f:
        f.write('\n'.join(lines))
        f.write('\n')


def extract_instances_for_word(senses, wiki_dir='../datasets/wiki/'):
    LOGGER.info(u"Processing word: %s" % senses[0]['word'].decode('utf-8'))
    instances = []
    instances_replaced = []
    instances_all_replaced = []
    sense_keys = []
    sense_key_all_replaced = []
    for sense_args in senses:
        sense_instances, sense_instances_replaced, sense_instances_all_replaced = extract_from_page(**sense_args)
        instances.extend(sense_instances)
        instances_replaced.extend(sense_instances_replaced)
        instances_all_replaced.extend(sense_instances_all_replaced)
        sense_key_all_replaced.extend([sense_args['offset']] * len(sense_instances_all_replaced))
        sense_keys.extend([sense_args['offset']] * len(sense_instances))

    # TODO: create a file in ..datasets/wiki/ and write instances.
    # original version
    write2file(os.path.join(wiki_dir, u'%s.txt' % senses[0]['word']), instances)
    # target word replaced version (e.g., dogs, DOG, Dog are replaced by 'dog')
    write2file(os.path.join(wiki_dir, u'%s.replaced.txt' % senses[0]['word']), instances_replaced)
    # replaced version of target word over all occurrences.
    write2file(os.path.join(wiki_dir, u'%s.replaced-all.txt' % senses[0]['word']), instances_all_replaced)
    write2file(os.path.join(wiki_dir, u'%s.replaced-all.key' % senses[0]['word']), sense_key_all_replaced)
    write2file(os.path.join(wiki_dir, u'%s.key' % senses[0]['word']), sense_keys)


def get_next_page_url(soup):
    # here we assume that next link is always in -6 index.
    element = soup.select_one('#mw-content-text').find_all('a')[-6]
    if element.text.startswith('next'):
        return u"{}{}".format(BASE_URL, element['href'])
    else:
        # No more element left.
        return None


def fetch_what_links_here(title, limit=1000, fetch_link_size=5000):
    # Max fetch link size is 5000.
    global SLEEP_INTERVAL

    fetch_link_size = min(limit, fetch_link_size)
    all_links = []
    next_page_url = WHAT_LINKS_HERE_URL.format(title, fetch_link_size)
    total_link_processed = 0
    while total_link_processed < limit and next_page_url is not None:
        LOGGER.debug(u"Processing link: %s" % next_page_url.decode('utf-8'))
        try:
            response = requests.get(next_page_url)
            content = response.content
            SLEEP_INTERVAL = 1
        except (ConnectionError, WikipediaException) as e:
            SLEEP_INTERVAL *= 2
            LOGGER.info(u"Sleeping {} seconds for {}. Reason: {}".format(SLEEP_INTERVAL, title.decode('utf-8'), e))
            sleep(SLEEP_INTERVAL)
            continue  # try at the beginning
        if response.status_code == 200:
            soup = BeautifulSoup(content, 'html.parser')
            rows = soup.find(id='mw-whatlinkshere-list').find_all('li', recursive=False)
            links = [row.find('a')['href'] for row in rows]
            next_page_url = get_next_page_url(soup)
            total_link_processed += len(links)
            all_links.extend(links)
        else:
            LOGGER.error(u"Error while link fetching: %s" % next_page_url.decode('utf-8'))

    return all_links


def extract_from_file(filename, num_process):
    import utils

    global LOGGER
    LOGGER = utils.get_logger()

    dataset_path = '../datasets/wiki'
    # get processed words
    processed_words = set([word.split('.')[0] for word in
                           filter(lambda x: x.endswith('.replaced-all.txt'), os.listdir(dataset_path))])

    jobs = dd(list)
    for line in open(filename):
        line = line.split()
        target_word, page_title, offset = line[:3]
        if target_word not in processed_words:
            jobs[target_word].append(dict(word=target_word, page_title=page_title, offset=offset, fetch_links=True))

    LOGGER.info("Total {} of jobs available. Num of consumer = {}".format(len(jobs), num_process))
    if num_process > 1:
        pool = Pool(num_process)
        pool.map(extract_instances_for_word, jobs.values())
    else:
        for v in jobs.values():
            extract_instances_for_word(v)

    LOGGER.info("Done.")


if __name__ == '__main__':
    pass
