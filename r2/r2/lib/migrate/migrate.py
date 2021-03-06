# The contents of this file are subject to the Common Public Attribution
# License Version 1.0. (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
# http://code.reddit.com/LICENSE. The License is based on the Mozilla Public
# License Version 1.1, but Sections 14 and 15 have been added to cover use of
# software over a computer network and provide for limited attribution for the
# Original Developer. In addition, Exhibit A has been modified to be consistent
# with Exhibit B.
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License for
# the specific language governing rights and limitations under the License.
#
# The Original Code is Reddit.
#
# The Original Developer is the Initial Developer.  The Initial Developer of the
# Original Code is CondeNet, Inc.
#
# All portions of the code written by CondeNet are Copyright (c) 2006-2010
# CondeNet, Inc. All Rights Reserved.
################################################################################
"""
One-time use functions to migrate from one reddit-version to another
"""
from r2.lib.promote import *

def add_allow_top_to_srs():
    "Add the allow_top property to all stored subreddits"
    from r2.models import Subreddit
    from r2.lib.db.operators import desc
    from r2.lib.utils import fetch_things2

    q = Subreddit._query(Subreddit.c._spam == (True,False),
                         sort = desc('_date'))
    for sr in fetch_things2(q):
        sr.allow_top = True; sr._commit()

def subscribe_to_blog_and_annoucements(filename):
    import re
    from time import sleep
    from r2.models import Account, Subreddit

    r_blog = Subreddit._by_name("blog")
    r_announcements = Subreddit._by_name("announcements")

    contents = file(filename).read()
    numbers = [ int(s) for s in re.findall("\d+", contents) ]

#    d = Account._byID(numbers, data=True)

#   for i, account in enumerate(d.values()):
    for i, account_id in enumerate(numbers):
        account = Account._byID(account_id, data=True)

        for sr in r_blog, r_announcements:
            if sr.add_subscriber(account):
                sr._incr("_ups", 1)
                print ("%d: subscribed %s to %s" % (i, account.name, sr.name))
            else:
                print ("%d: didn't subscribe %s to %s" % (i, account.name, sr.name))


def upgrade_messages(update_comments = True, update_messages = True,
                     update_trees = True):
    from r2.lib.db import queries
    from r2.lib import comment_tree, cache
    from r2.models import Account
    from pylons import g
    accounts = set()

    def batch_fn(items):
        g.reset_caches()
        return items
    
    if update_messages or update_trees:
        q = Message._query(Message.c.new == True,
                           sort = desc("_date"),
                           data = True)
        for m in fetch_things2(q, batch_fn = batch_fn):
            print m,m._date
            if update_messages:
                accounts = accounts | queries.set_unread(m, m.new)
            else:
                accounts.add(m.to_id)
    if update_comments:
        q = Comment._query(Comment.c.new == True,
                           sort = desc("_date"))
        q._filter(Comment.c._id < 26152162676)

        for m in fetch_things2(q, batch_fn = batch_fn):
            print m,m._date
            queries.set_unread(m, True)

    print "Precomputing comment trees for %d accounts" % len(accounts)

    for i, a in enumerate(accounts):
        if not isinstance(a, Account):
            a = Account._byID(a)
        print i, a
        comment_tree.user_messages(a)

def recompute_unread(min_date = None):
    from r2.models import Inbox, Account, Comment, Message
    from r2.lib.db import queries

    def load_accounts(inbox_rel):
        accounts = set()
        q = inbox_rel._query(eager_load = False, data = False,
                             sort = desc("_date"))
        if min_date:
            q._filter(inbox_rel.c._date > min_date)

        for i in fetch_things2(q):
            accounts.add(i._thing1_id)

        return accounts

    accounts_m = load_accounts(Inbox.rel(Account, Message))
    for i, a in enumerate(accounts_m):
        a = Account._byID(a)
        print "%s / %s : %s" % (i, len(accounts_m), a)
        queries.get_unread_messages(a).update()
        queries.get_unread_comments(a).update()
        queries.get_unread_selfreply(a).update()

    accounts = load_accounts(Inbox.rel(Account, Comment)) - accounts_m
    for i, a in enumerate(accounts):
        a = Account._byID(a)
        print "%s / %s : %s" % (i, len(accounts), a)
        queries.get_unread_comments(a).update()
        queries.get_unread_selfreply(a).update()



def pushup_permacache(verbosity=1000):
    """When putting cassandra into the permacache chain, we need to
       push everything up into the rest of the chain, so this is
       everything that uses the permacache, as of that check-in."""
    from pylons import g
    from r2.models import Link, Subreddit, Account
    from r2.lib.db.operators import desc
    from r2.lib.comment_tree import comments_key, messages_key
    from r2.lib.utils import fetch_things2, in_chunks
    from r2.lib.utils import last_modified_key
    from r2.lib.promote import promoted_memo_key
    from r2.lib.subreddit_search import load_all_reddits
    from r2.lib.db import queries
    from r2.lib.cache import CassandraCacheChain

    authority = g.permacache.caches[-1]
    nonauthority = CassandraCacheChain(g.permacache.caches[1:-1])

    def populate(keys):
        vals = authority.simple_get_multi(keys)
        if vals:
            nonauthority.set_multi(vals)

    def gen_keys():
        yield promoted_memo_key

        # just let this one do its own writing
        load_all_reddits()

        yield queries.get_all_comments().iden

        l_q = Link._query(Link.c._spam == (True, False),
                          Link.c._deleted == (True, False),
                          sort=desc('_date'),
                          data=True,
                          )
        for link in fetch_things2(l_q, verbosity):
            yield comments_key(link._id)
            yield last_modified_key(link, 'comments')

        a_q = Account._query(Account.c._spam == (True, False),
                             sort=desc('_date'),
                             )
        for account in fetch_things2(a_q, verbosity):
            yield messages_key(account._id)
            yield last_modified_key(account, 'overview')
            yield last_modified_key(account, 'commented')
            yield last_modified_key(account, 'submitted')
            yield last_modified_key(account, 'liked')
            yield last_modified_key(account, 'disliked')
            yield queries.get_comments(account, 'new', 'all').iden
            yield queries.get_submitted(account, 'new', 'all').iden
            yield queries.get_liked(account).iden
            yield queries.get_disliked(account).iden
            yield queries.get_hidden(account).iden
            yield queries.get_saved(account).iden
            yield queries.get_inbox_messages(account).iden
            yield queries.get_unread_messages(account).iden
            yield queries.get_inbox_comments(account).iden
            yield queries.get_unread_comments(account).iden
            yield queries.get_inbox_selfreply(account).iden
            yield queries.get_unread_selfreply(account).iden
            yield queries.get_sent(account).iden

        sr_q = Subreddit._query(Subreddit.c._spam == (True, False),
                                sort=desc('_date'),
                                )
        for sr in fetch_things2(sr_q, verbosity):
            yield last_modified_key(sr, 'stylesheet_contents')
            yield queries.get_links(sr, 'hot', 'all').iden
            yield queries.get_links(sr, 'new', 'all').iden

            for sort in 'top', 'controversial':
                for time in 'hour', 'day', 'week', 'month', 'year', 'all':
                    yield queries.get_links(sr, sort, time,
                                            merge_batched=False).iden
            yield queries.get_spam_links(sr).iden
            yield queries.get_spam_comments(sr).iden
            yield queries.get_reported_links(sr).iden
            yield queries.get_reported_comments(sr).iden
            yield queries.get_subreddit_messages(sr).iden
            yield queries.get_unread_subreddit_messages(sr).iden

    done = 0
    for keys in in_chunks(gen_keys(), verbosity):
        g.reset_caches()
        done += len(keys)
        print 'Done %d: %r' % (done, keys[-1])
        populate(keys)

# alter table bids DROP constraint bids_pkey;
# alter table bids add column campaign integer;
# update bids set campaign = 0;
# alter table bids ADD primary key (transaction, campaign);
def promote_v2():
    # alter table bids add column campaign integer;
    # update bids set campaign = 0; 
    from r2.models import Link, NotFound, PromoteDates, Bid
    from datetime import datetime
    from pylons import g
    for p in PromoteDates.query():
        try:
            l = Link._by_fullname(p.thing_name,
                                  data = True, return_dict = False)
            if not l:
                raise NotFound, p.thing_name

            # update the promote status
            l.promoted = True
            l.promote_status = getattr(l, "promote_status", STATUS.unseen)
            l._date = datetime(*(list(p.start_date.timetuple()[:7]) + [g.tz]))
            set_status(l, l.promote_status)

            # add new campaign
            print (l, (p.start_date, p.end_date), p.bid, None)
            if not p.bid:
                print "no bid? ", l
                p.bid = 20
            new_campaign(l, (p.start_date, p.end_date), p.bid, None)
            print "updated: %s (%s)" % (l, l._date)

        except NotFound:
            print "NotFound: %s" % p.thing_name

    print "updating campaigns"
    for b in Bid.query():
        l = Link._byID(int(b.thing_id))
        print "updating: ", l
        campaigns = getattr(l, "campaigns", {}).copy()
        indx = b.campaign
        if indx in campaigns:
            sd, ed, bid, sr, trans_id = campaigns[indx]
            campaigns[indx] = sd, ed, bid, sr, b.transaction
            l.campaigns = campaigns
            l._commit()
        else:
            print "no campaign information: ", l

def port_cassavotes():
    from r2.models import Vote, Account, Link, Comment
    from r2.models.vote import CassandraVote, CassandraLinkVote, CassandraCommentVote
    from r2.lib.db.tdb_cassandra import CL
    from r2.lib.utils import fetch_things2, to36, progress

    ts = [(Vote.rel(Account, Link), CassandraLinkVote),
          (Vote.rel(Account, Comment), CassandraCommentVote)]

    dataattrs = set(['valid_user', 'valid_thing', 'ip', 'organic'])

    for prel, crel in ts:
        vq = prel._query(sort=desc('_date'),
                         data=True,
                         eager_load=False)
        vq = fetch_things2(vq)
        vq = progress(vq, persec=True)
        for v in vq:
            t1 = to36(v._thing1_id)
            t2 = to36(v._thing2_id)
            cv = crel(thing1_id = t1,
                      thing2_id = t2,
                      date=v._date,
                      name=v._name)
            for dkey, dval in v._t.iteritems():
                if dkey in dataattrs:
                    setattr(cv, dkey, dval)

            cv._commit(write_consistency_level=CL.ONE)

def port_cassasaves(after_id=None, estimate=12489897):
    from r2.models import SaveHide, CassandraSave
    from r2.lib.db.operators import desc
    from r2.lib.db.tdb_cassandra import CL
    from r2.lib.utils import fetch_things2, to36, progress

    q = SaveHide._query(
        SaveHide.c._name == 'save',
        sort=desc('_date'),
        data=False,
        eager_load=False)

    if after_id is not None:
        q._after(SaveHide._byID(after_id))

    for sh in progress(fetch_things2(q), estimate=estimate):

        csh = CassandraSave(thing1_id = to36(sh._thing1_id),
                            thing2_id = to36(sh._thing2_id),
                            date = sh._date)
        csh._commit(write_consistency_level = CL.ONE)

def port_cassaurls(after_id=None, estimate=15231317):
    from r2.models import Link, LinksByUrl
    from r2.lib.db import tdb_cassandra
    from r2.lib.db.operators import desc
    from r2.lib.db.tdb_cassandra import CL
    from r2.lib.utils import fetch_things2, in_chunks, progress

    q = Link._query(Link.c._spam == (True, False),
                    sort=desc('_date'), data=True)
    if after_id:
        q._after(Link._byID(after_id,data=True))
    q = fetch_things2(q, chunk_size=500)
    q = progress(q, estimate=estimate)
    q = (l for l in q
         if getattr(l, 'url', 'self') != 'self'
         and not getattr(l, 'is_self', False))
    chunks = in_chunks(q, 500)

    for chunk in chunks:
        with LinksByUrl._cf.batch(write_consistency_level = CL.ONE) as b:
            for l in chunk:
                k = LinksByUrl._key_from_url(l.url)
                if k:
                    b.insert(k, {l._id36: l._id36})

def port_cassahides():
    from r2.models import SaveHide, CassandraHide
    from r2.lib.db.tdb_cassandra import CL
    from r2.lib.db.operators import desc
    from r2.lib.utils import fetch_things2, timeago, progress

    q = SaveHide._query(SaveHide.c._date > timeago('1 week'),
                        SaveHide.c._name == 'hide',
                        sort=desc('_date'))
    q = fetch_things2(q)
    q = progress(q, estimate=1953374)

    for sh in q:
        CassandraHide._hide(sh._thing1, sh._thing2,
                            write_consistency_level=CL.ONE)
