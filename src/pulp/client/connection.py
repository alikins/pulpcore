#
# A proxy interface to initiate and interact communication with Pulp Server.
#
# Copyright (c) 2010 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public License,
# version 2 (GPLv2). There is NO WARRANTY for this software, express or
# implied, including the implied warranties of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. You should have received a copy of GPLv2
# along with this software; if not, see
# http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.
#
# Red Hat trademarks are not licensed under GPLv2. No permission is
# granted to use or replicate Red Hat trademarks that are incorporated
# in this software or its documentation.
#

import base64
import httplib
import locale
import sys
import os

from gettext import gettext as _

try:
    import json
except ImportError:
    import simplejson as json

from M2Crypto import SSL, httpslib

from pulp.client.logutil import getLogger


log = getLogger(__name__)

consumer_deferred_fields = ['package_profile', 'repoids']
package_deferred_fields = []
repository_deferred_fields = ['packages', 'packagegroups', 'packagegroupcategories']


def realpath(path):
    if os.path.exists(path):
        return path
    else:
        return None


class RestlibException(Exception):
    def __init__(self, code, msg=""):
        self.code = code
        self.msg = msg

    def __str__(self):
        return '%s: %s' % (str(self.code), self.msg)

class Restlib(object):
    """
     A wrapper around httplib to make rest calls easier
    """
    def __init__(self, host, port, apihandler, cert_file=None, key_file=None,
                 username=None, password=None):
        self.host = host
        # ensure we have an integer, httpslib is picky about the type
        # passed in for the port
        self.port = int(port)
        self.apihandler = apihandler
        self.username = username
        self.password = password
        if (self.username != None):
            raw = "%s:%s" % (self.username, self.password)
            base64string = base64.encodestring(raw)[:-1]
            auth = "Basic %s" % base64string
        else:
            auth = None
        self.headers = {"Content-type":"application/json",
                        "Authorization": auth,
                        "Accept": "application/json",
                        "Accept-Language": locale.getdefaultlocale()[0].lower().replace('_', '-')}
        self.cert_file = cert_file
        self.key_file = key_file

    def _request(self, request_type, method, info=None):
        handler = method
        if not handler.startswith(self.apihandler):
            #handler = self.apihandler + handler
            handler = '/'.join((self.apihandler, handler))
        log.debug("_request calling: %s to host:port : %s:%s" %
                  (handler, self.host, self.port))
        if self.cert_file:
            log.info("Using SSLv3 context")
            context = SSL.Context("sslv3")
            context.load_cert(self.cert_file, keyfile=self.key_file)
            conn = httpslib.HTTPSConnection(self.host, self.port, ssl_context=context)
        else:
            conn = httplib.HTTPSConnection(self.host, self.port)
        log.debug("Request_type: %s" % request_type)
        log.debug("info: %s" % info)
        log.debug("headers: %s" % self.headers)
        conn.request(request_type, handler, body=json.dumps(info),
                     headers=self.headers)
        response = conn.getresponse()
        if response.status == 404:
            return None
        self.validateResponse(response)
        rinfo = response.read()
        if not len(rinfo):
            return None
        return json.loads(rinfo)

    def validateResponse(self, response):
        if response.status not in [200, 201, 202, 204]:
            raise RestlibException(response.status, response.read())
            #parsed = json.loads(response.read())

    def request_get(self, method):
        return self._request("GET", method)

    def request_post(self, method, params=""):
        return self._request("POST", method, params)

    def request_head(self, method):
        return self._request("HEAD", method)

    def request_put(self, method, params=""):
        return self._request("PUT", method, params)

    def request_delete(self, method):
        return self._request("DELETE", method)

class PulpConnection:
    """
    Proxy connection to Pulp Server
    """

    CERT_PATH = "/etc/pki/consumer/cert.pem"
    KEY_PATH = "/etc/pki/consumer/key.pem"

    def __init__(self, host='localhost', port=443, handler="/pulp/api",
            cert_file=realpath(CERT_PATH), key_file=realpath(KEY_PATH),
            username=None, password=None):
        self.host = host
        self.port = port
        self.handler = handler
        self.conn = None
        self.cert_file = cert_file
        self.key_file = key_file
        self.username = username
        self.password = password
        # initialize connection
        self.setUp()

    def setUp(self):
        self.conn = Restlib(self.host, self.port, self.handler, self.cert_file,
                            self.key_file, self.username, self.password)
        log.info("Connection Established for cli: Host: %s, Port: %s, handler: %s" %
                 (self.host, self.port, self.handler))
        log.info("Using cert_file: %s and key_file: %s" %
                 (self.cert_file, self.key_file))

    def shutDown(self):
        self.conn.close()
        log.info("remote connection closed")


class RepoConnection(PulpConnection):
    """
    Connection class to access repo specific calls
    """
    def create(self, id, name, arch, feed=None, symlinks=False,
               sync_schedule=None, cert_data=None):
        method = "/repositories/"
        repodata = {"id"   : id,
                    "name" : name,
                    "arch" : arch,
                    "feed" : feed,
                    "use_symlinks" : symlinks,
                    "sync_schedule" : sync_schedule,
                    "cert_data"     : cert_data}
        return self.conn.request_put(method, params=repodata)

    def repository(self, id, fields=None):
        method = "/repositories/%s/" % str(id)
        repo = self.conn.request_get(method)
        if repo is None:
            return None
        for field in repository_deferred_fields:
            repo[field] = self.conn.request_get('%s%s/' % (method, field))
        return repo

    def repositories(self):
        method = "/repositories/"
        return self.conn.request_get(method)

    def update(self, repo):
        method = "/repositories/%s/" % repo['id']
        return self.conn.request_put(method, params=repo)

    def delete(self, id):
        method = "/repositories/%s/" % id
        return self.conn.request_delete(method)

    def clean(self):
        method = "/repositories/"
        return self.conn.request_delete(method)

    def sync(self, repoid, timeout=None):
        method = "/repositories/%s/sync/" % repoid
        return self.conn.request_post(method, params={"timeout":timeout})

    def add_package(self, repoid, packageid):
        addinfo = {'repoid' : repoid,
                      'packageid' : packageid}
        method = "/repositories/%s/add_package/" % repoid
        return self.conn.request_post(method, params=addinfo)

    def get_package(self, repoid, pkg_name):
        method = "/repositories/%s/get_package/" % repoid
        return self.conn.request_post(method, params=pkg_name)

    def packages(self, repoid):
        method = "/repositories/%s/packages/" % repoid
        return self.conn.request_get(method)

    def packagegroups(self, repoid):
        method = "/repositories/%s/packagegroups/" % repoid
        return self.conn.request_get(method)

    def create_packagegroup(self, repoid, groupid, groupname, description):
        method = "/repositories/%s/create_packagegroup/" % repoid
        return self.conn.request_post(method, params={"groupid":groupid,
            "groupname":groupname, "description":description})

    def delete_packagegroup(self, repoid, groupid):
        method = "/repositories/%s/delete_packagegroup/" % repoid
        return self.conn.request_post(method, params={"groupid":groupid})

    def add_package_to_group(self, repoid, groupid, pkgname, gtype):
        method = "/repositories/%s/add_package_to_group/" % repoid
        return self.conn.request_post(method,
                params={"groupid":groupid, "name":pkgname, "type":gtype})

    def delete_package_from_group(self, repoid, groupid, pkgname, gtype):
        method = "/repositories/%s/delete_package_from_group/" % repoid
        return self.conn.request_post(method,
                params={"groupid":groupid, "name":pkgname, "type":gtype})

    def upload(self, id, pkginfo, pkgstream):
        uploadinfo = {'repo' : id,
                      'pkginfo' : pkginfo,
                      'pkgstream' : pkgstream}
        method = "/repositories/%s/upload/" % id
        return self.conn.request_post(method, params=uploadinfo)

    def all_schedules(self):
        method = "/repositories/schedules/"
        return self.conn.request_get(method)

    def sync_status(self, status_path):
        return self.conn.request_get(status_path)

    def add_errata(self, id, errataids):
        erratainfo = {'repoid' : id,
                      'errataid' : errataids}
        method = "/repositories/%s/add_errata/" % id
        return self.conn.request_post(method, params=erratainfo)

    def delete_errata(self, id, errataids):
        erratainfo = {'repoid' : id,
                      'errataid' : errataids}
        method = "/repositories/%s/delete_errata/" % id
        return self.conn.request_post(method, params=erratainfo)

    def errata(self, id, types=[]):
        erratainfo = {'repoid' : id,
                      'types' : types}
        method = "/repositories/%s/list_errata/" % id
        return self.conn.request_post(method, params=erratainfo)


class ConsumerConnection(PulpConnection):
    """
    Connection class to access repo specific calls
    """
    def create(self, id, description):
        consumerdata = {"id"   : id, "description" : description}
        method = "/consumers/"
        return self.conn.request_put(method, params=consumerdata)

    def update(self, consumer):
        method = "/consumers/%s/" % consumer['id']
        return self.conn.request_put(method, params=consumer)

    def bulkcreate(self, consumers):
        method = "/consumers/bulk/"
        return self.conn.request_post(method, params=consumers)

    def delete(self, id):
        method = "/consumers/%s/" % id
        return self.conn.request_delete(method)

    def clean(self):
        method = "/consumers/"
        return self.conn.request_delete(method)

    def consumer(self, id):
        method = "/consumers/%s/" % str(id)
        consumer = self.conn.request_get(method)
        for field in consumer_deferred_fields:
            consumer[field] = self.conn.request_get('%s%s/' % (method, field))
        return consumer

    def packages(self, id):
        method = "/consumers/%s/packages/" % str(id)
        return self.conn.request_get(method)

    def certificate(self, id):
        method = "/consumers/%s/certificate/" % str(id)
        cert_dict = self.conn.request_get(method)
        return cert_dict

    def consumers(self):
        method = "/consumers/"
        return self.conn.request_get(method)

    def consumers_with_package_name(self, name):
        method = '/consumers/?package_name=%s' % name
        return self.conn.request_get(method)

    def bind(self, id, repoid):
        method = "/consumers/%s/bind/" % id
        return self.conn.request_post(method, params=repoid)

    def unbind(self, id, repoid):
        method = "/consumers/%s/unbind/" % id
        return self.conn.request_post(method, params=repoid)

    def profile(self, id, profile):
        method = "/consumers/%s/profile/" % id
        return self.conn.request_post(method, params=profile)

    def installpackages(self, id, packagenames):
        method = "/consumers/%s/installpackages/" % id
        body = dict(packagenames=packagenames)
        return self.conn.request_post(method, params=body)

    def installpackagegroups(self, id, packageids):
        method = "/consumers/%s/installpackagegroups/" % id
        body = dict(packageids=packageids)
        return self.conn.request_post(method, params=body)

    def errata(self, id, types=None):
        method = "/consumers/%s/listerrata/" % id
        body = dict(types=types)
        return self.conn.request_post(method, params=body)

    def installerrata(self, id, errataids, types=[]):
        erratainfo = {'consumerid' : id,
                      'errataids' : errataids,
                      'types'    :   types}
        method = "/consumers/%s/installerrata/" % id
        return self.conn.request_post(method, params=erratainfo)


class ConsumerGroupConnection(PulpConnection):
    """
    Connection class to access consumer group related calls
    """
    def create(self, id, description, consumerids=[]):
        consumergroup_data = {"id" : id, "description" : description,
                        "consumerids" : consumerids}
        method = "/consumergroups/"
        return self.conn.request_put(method, params=consumergroup_data)

    def update(self, consumergroup):
        method = "/consumergroups/%s/" % consumergroup['id']
        return self.conn.request_put(method, params=consumergroup)

    def delete(self, id):
        method = "/consumergroups/%s/" % id
        return self.conn.request_delete(method)

    def clean(self):
        method = "/consumergroups/"
        return self.conn.request_delete(method)

    def consumergroups(self):
        method = "/consumergroups/"
        return self.conn.request_get(method)

    def consumergroup(self, id):
        method = "/consumergroups/%s/" % str(id)
        return self.conn.request_get(method)

    def add_consumer(self, id, consumerid):
        method = "/consumergroups/%s/add_consumer/" % id
        return self.conn.request_post(method, params=consumerid)

    def delete_consumer(self, id, consumerid):
        method = "/consumers/%s/delete_consumer/" % id
        return self.conn.request_post(method, params=consumerid)

    def bind(self, id, repoid):
        method = "/consumergroups/%s/bind/" % id
        return self.conn.request_post(method, params=repoid)

    def unbind(self, id, repoid):
        method = "/consumergroups/%s/unbind/" % id
        return self.conn.request_post(method, params=repoid)

    def installpackages(self, id, packagenames):
        method = "/consumergroups/%s/installpackages/" % id
        body = dict(packagenames=packagenames)
        return self.conn.request_post(method, params=body)

    def installerrata(self, id, errataids, types=[]):
        erratainfo = {'consumerid' : id,
                      'errataids' : errataids,
                      'types'    :   types}
        method = "/consumergroups/%s/installerrata/" % id
        return self.conn.request_post(method, params=erratainfo)

class PackageConnection(PulpConnection):

    def clean(self):
        method = "/packages/"
        return self.conn.request_delete(method)

    def create(self, name, epoch, version, release, arch, description,
            checksum_type, checksum, filename):
        method = "/packages/"
        repodata = {"name"   : name,
                    "epoch" : epoch,
                    "version" : version,
                    "release" : release,
                    "arch" : arch,
                    "description" : description,
                    "checksum_type" : checksum_type,
                    "checksum": checksum,
                    "filename": filename, }
        return self.conn.request_put(method, params=repodata)

    def packages(self):
        method = "/packages/"
        return self.conn.request_get(method)

    def package(self, id, filter=None):
        method = "/packages/%s/" % id
        return self.conn.request_get(method)

    def delete(self, packageid):
        method = "/packages/%s/" % packageid
        return self.conn.request_delete(method)

    def package_by_ivera(self, name, version, release, epoch, arch):
        method = "/packages/%s/%s/%s/%s/%s/" % (name, version, release, epoch, arch)
        return self.conn.request_get(method)

class PackageGroupConnection(PulpConnection):

    def clean(self):
        pass


class PackageGroupCategoryConnection(PulpConnection):

    def clean(self):
        pass


class UserConnection(PulpConnection):
    """
    Connection class to access consumer group related calls
    """
    def create(self, login, password=None, name=None):
        user_data = {"login" : login, "password" : password,
                        "name" : name}
        method = "/users/"
        return self.conn.request_put(method, params=user_data)

    def update(self, user):
        method = "/users/%s/" % user['id']
        return self.conn.request_put(method, params=user)

    def delete(self, **kwargs):
        login = kwargs['login']
        method = "/users/%s/" % login
        return self.conn.request_delete(method)

    def clean(self):
        method = "/users/"
        return self.conn.request_delete(method)

    def users(self):
        method = "/users/"
        return self.conn.request_get(method)

    def user(self, login):
        method = "/users/%s/" % str(login)
        return self.conn.request_get(method)


class ErrataConnection(PulpConnection):
    """
    Connection class to access errata related calls
    """
    def clean(self):
        pass

    def create(self, id, title, description, version, release, type,
            status="", updated="", issued="", pushcount="", update_id="",
            from_str="", reboot_suggested="", references=[],
            pkglist=[]):
        pass

    def erratum(self, id):
        method = "/errata/%s/" % id
        return self.conn.request_get(method)

    def errata(self, id=None, title=None, description=None, version=None,
            release=None, type=None, status=None, updated=None, issued=None,
            pushcount=None, from_str=None, reboot_suggested=None):
        pass

if __name__ == '__main__':
    rconn = RepoConnection()
    print "+--------------------------------+"
    print "   Repo API Tests                "
    print "+--------------------------------+"

    repo = rconn.create('test-f12', 'f12', 'i386', 'yum:http://mmccune.fedorapeople.org/pulp/')
    print "create Repos", repo['id']
    print "list repos:", rconn.repositories()
    print "Get repo By Id: ", rconn.repository(repo['id'])
    newdata = {'id' : 'test-f12',
                'name' : 'f12',
                'arch' : 'noarch',
                'feed' : 'yum:http://mmccune.fedorapeople.org/pulp/'}
    #print "update Repo:",rconn.update(repo['id'], newdata)
    print "Sync Repos:", rconn.sync(repo['id'])
    print "list Repo Packages: ", rconn.packages(repo['id'])
    print "delete Repo:", rconn.delete(repo['id'])
    print "+--------------------------------+"
    print "   Consumer API Tests             "
    print "+--------------------------------+"
    cconn = ConsumerConnection()
    print "Create Consumer", cconn.create("test", 'prad.rdu.redhat.com')
    print "List Consumers", cconn.consumers()
