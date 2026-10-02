#! /usr/bin/env python

import sys
import subprocess
import custom
py3 = sys.version >= "3."
py2 = not py3

import collections
from datetime import datetime,timezone
if py3:
    import urllib.parse as urlparse
else:
    import urlparse

import re

Debug = False

if not Debug:               # debug, uncomment 

    import samweb_client

    from rucio.client.replicaclient import ReplicaClient
    from rucio.client.didclient import DIDClient
    from rucio.client.ruleclient import RuleClient
    from rucio.common.exception import DataIdentifierNotFound, DataIdentifierAlreadyExists, FileAlreadyExists, DuplicateRule, RucioException, AccessDenied, DuplicateContent

    from metacat.webapi import MetaCatClient
    from metacat.webapi.webapi import NotFoundError, InvalidMetadataError, AlreadyExistsError 
    metaclient = MetaCatClient(server_url="https://metacat.fnal.gov:9443/dune_meta_prod/app", auth_server_url="https://metacat.fnal.gov:8143/auth/dune")


    samweb = samweb_client.SAMWebClient('dune')

    def _chunk(iterator, chunksize): 
        """ Helper to divide an iterator into chunks of a given size """ 
        from itertools import islice 
        while True: 
            l = list(islice(iterator, chunksize)) 
            if l: yield l 
            else: return 

    def convert_enstore_to_adler32(encrc, filesize):
        """ convert an enstore checksum to an adler32 one """
        if isinstance(encrc, str):
            try:
                tp, encrc = encrc.split(':',1)
                if tp != 'enstore':
                    raise Exception('Unable to convert %s checksum to adler32' % tp)
            except ValueError:
                pass # no ':'
        try:
            encrc = int(encrc)
        except ValueError:
            raise ChecksumError("Invalid enstore checksum value: %s" % encrc)

        BASE = 65521
        sz = int(filesize % BASE)
        s1 = (encrc & 0xffff)
        s2 = ((encrc >> 16) & 0xffff)
        s1 = (s1 + 1) % BASE
        s2 = (sz + s2) % BASE
        adler32 = (s2 << 16) + s1
        return '%08x' % adler32

    FileInfo = collections.namedtuple('FileInfo', 'url file_name file_size adler32 md5')

    def get_sam_files(defname, run_number, runlist):

        i = 0
        if runlist == False:
            run_number = None
        if run_number is not None:
            filelist = samweb.listFilesAndLocations(dimensions='defname: %s and run_number %d' % (defname, run_number), filter_path=['enstore:', 'cern-eos:', 'castor:'], checksums=True, schema=['root'])
        else:
# todo  need to bring back the filter_path as a CLI option
#            filelist = samweb.listFilesAndLocations(defname=defname, filter_path=['enstore:', 'cern-eos:', 'castor:'], checksums=True, schema=['root'])
            filelist = samweb.listFilesAndLocations(defname=defname, checksums=True, schema=['root'])

        for loc in filelist:
            print(loc)
            if(len(loc)) == 5:
                url, samloc, file_name, file_size, checksums = loc
                if isinstance(file_size, str):
                    file_size = int(file_size)
                adler32 = md5 = None
                checksums = dict( c.split(':', 1) for c in checksums )
                try:
                    adler32 = checksums['adler32']
                except KeyError:
                    ecrc = checksums.get('enstore')
                    if ecrc is not None:
                        adler32 = convert_enstore_to_adler32(ecrc, file_size)
                yield FileInfo(url, file_name, file_size, adler32, md5)
            elif(len(loc)) == 4:
                url, samloc, file_name, file_size = loc
                if isinstance(file_size[0], str):
                    file_size = int(file_size[0])
#           have to grab the checksum from xrdadler32 since missing in SAM
                xrdoutput = subprocess.run( [ 'xrdadler32', url] , capture_output=True, text=True, check=True).stdout
                checksum, junkurl = xrdoutput.split(' ')
                adler32=checksum
                md5=None
                yield FileInfo(url, file_name, file_size, adler32, md5)
            else:
                print(loc,"missing fields in location record, skipping")

            i+=1
            if i > sys.maxsize: return
    rse_map =  { "fndcadoor.fnal.gov": {"/dune/tape_backed/dunepro": {"rse_name": "FNAL_DCACHE"},
                                     "/dune/archive/sam_managed_users": {"rse_name": "FNAL_MISC_TAPE"},
                                     "/dune/tape_backed/users": {"rse_name": "FNAL_MISC_TAPE_USERS"},
                                     "/dune/tape_backed/mc_backup": {"rse_name": "FNAL_MISC_TAPE_MC_BACKUP"},
                                     "/lbne": {"rse_name": "FNAL_MISC_TAPE_LBNE"},},
              "eospublicftp.cern.ch": {"/eos/experiment/neutplatform/protodune": {"rse_name": "CERN_PDUNE_EOS" },},
              "eospublic.cern.ch": {"/eos/experiment/neutplatform/protodune": {"rse_name": "CERN_PDUNE_EOS" },
                                    "/eos/experiment/neutplatform/protodune/dune": {"rse_name": "DUNE_CERN_EOS" },},
              "eosctapublic.cern.ch": {"/eos/ctapublic/archive/neutplatform/protodune/rawdata/np04": {"rse_name": "CERN_PDUNE_CASTOR"},},
            }                        
                 
#    rse_map = { 
#            'fndca1.fnal.gov': 'FNAL_DCACHE',
#            'fndcadoor.fnal.gov': 'FNAL_DCACHE',
#            'eospublicftp.cern.ch' : 'CERN_PDUNE_EOS',
#            'eospublic.cern.ch' : 'CERN_PDUNE_EOS',
#            'eosctapublic.cern.ch' : 'CERN_PDUNE_CASTOR',
#            'fndcadoor.fnal.gov': 'FNAL_DCACHE',
#} 

    def add_rule(ruleclient, rucio_dataset, rse, rucio_account):
        # add a rule
        try:
            if not dry_run:
                ruleclient.add_replication_rule([{'scope': scope, 'name' : rucio_dataset}], 1, rse, ignore_availability=True, account=rucio_account)
                print('%s replication rule added for %s:%s' % (rse, scope, rucio_dataset))
            else:
                print('Would add %s replication rule for %s:%s' % (rse, scope, rucio_dataset))
        except DuplicateRule:
            print('%s replication rule already exists' % rse)

    def add_run(samdef, run_number, rucio_account, scope, rucio_run_dataset, rucio_container, runlist, filter_runs, stream_container=None):

        print('Adding run number %d' % run_number)
        if runlist == True:
            rucio_dataset = rucio_run_dataset.format(run_number=run_number)
        else:
            rucio_dataset = rucio_run_dataset

        root_replicaclient = ReplicaClient(account='root') # with the current permission scheme root can add replicas anywhere
        didclient = DIDClient(account=rucio_account)
        ruleclient = RuleClient(account=rucio_account)


        knowndids = set()

        init_dataset = False
        eos_rule = False
        castor_rule = False
        fnal_rule = False
        fnal_misc_tape_rule = False
        fnal_misc_tape_users_rule = False
        fnal_misc_tape_mc_backup_rule = False
        fnal_misc_tape_lbne_rule = False

        data_stream = None

        found_files = False
        declared = None
        attached = None
        print ("Before get_sam_files")
        print(samdef, run_number)
        for filelist in _chunk(get_sam_files(samdef, run_number, runlist), 100):

            found_files = True
            print ("found some files")
            if not init_dataset:
                if not dry_run:
                    print( scope, rucio_dataset)                    
                    try:
                        metadid="%s:%s" % (scope,rucio_dataset)
                        metads=metaclient.get_dataset(metadid)
                    except NotFoundError:
                        metaclient.create_dataset(metadid)
                    print (metads)
                    try:
                        didclient.add_dataset(scope, rucio_dataset)
                    except DataIdentifierAlreadyExists:
                        dataset_contents = didclient.list_content(scope, rucio_dataset)
                        knowndids.update( (did["scope"],did["name"]) for did in dataset_contents )
                    except AccessDenied:
                        metaclient.create_dataset(metadid)
                        didclient.add_dataset(scope, rucio_dataset)
                else:
                    print('Would add dataset %s:%s' % (scope, rucio_dataset))
                    dataset_contents = didclient.list_content(scope, rucio_dataset)
                    knowndids.update( (did["scope"],did["name"]) for did in dataset_contents )
                init_dataset = True

            replicas_to_add = collections.defaultdict(list)
            dids_to_add = set()
            for fi in filelist:
                print(fi)
#  Check to make sure it is in metacat
                mcfilecheck=metaclient.get_file(name=fi.file_name,namespace=scope)
                print(mcfilecheck)               
# change code order here, change this if to do the metacat add first if necessary.
# We should get the SAM metadata fetched regardless of whether the file is in metacat or not,
# move that fetch to before the mcfilecheck.
# and then go on to process the sam-rucio
                md = samweb.getMetadata(fi.file_name)
                print(md)
                if mcfilecheck is None:
# Call the routines to convert sam metadata to metacat data and add it to metacat
                    metacat_meta = custom.metacat_metadata(fi.file_name,md)
                    file_id=md.get('file_id')
                    file_id=str(file_id)
                    print(metacat_meta)
#                    myparents=md.get("parents",[])
# this will only work if there's only one file in the parent list
#                    del myparents[0]['file_name']
#                    myparents[0]['fid']=str(myparents[0]['file_id'])
#                    del myparents[0]['file_id']
                    print (file_id,fi.file_name,fi.file_size,fi.adler32)
                    file_info = metaclient.declare_file(fid=file_id,namespace=scope,name=fi.file_name,
                               metadata=metacat_meta,dataset_did=metadid,
                               parents=[],
                               size=fi.file_size, checksums={"adler32": fi.adler32 }
                    )
#                if mcfilecheck is not None:
                if stream_container and not data_stream:
                    # lookup the datastream (it will be the same for the entire run)
#                    md = samweb.getMetadata(fi.file_name)
                    data_stream = md.get('data_stream')

                pfn = fi.url
                print(pfn)
                parsed_url = urlparse.urlparse(pfn)
                print (parsed_url.hostname,parsed_url.path)
                if parsed_url.hostname == 'castorpublic.cern.ch':
                    rse = rse_map['eosctapublic.cern.ch']['/eos/ctapublic/archive/neutplatform/protodune/rawdata/np04']['rse_name']
                    pfn = pfn.replace('root://castorpublic.cern.ch//castor/cern.ch/','root://eosctapublic.cern.ch//eos/ctapublic/archive/',1)
                    pfn = pfn.replace('rawdata/np02','rawdata/np04')
                elif parsed_url.hostname == 'fndcadoor.fnal.gov':
                    try:
                        dunestart=parsed_url.path.index('/dune')
                        shortpath=parsed_url.path[dunestart:]
                        patharray=shortpath.split('/')
                        threepath=("/%s/%s/%s") % (patharray[1], patharray[2], patharray[3])
                    except ValueError:
                        dunestart=parsed_url.path.index('/lbne')                   
                        shortpath=parsed_url.path[dunestart:]
                        threepath=("/lbne") 
                    print(threepath)
                    rse = rse_map[parsed_url.hostname][threepath]['rse_name']
                elif parsed_url.hostname == "eospublic.cern.ch":
                    rse = rse_map[parsed_url.hostname]['/eos/experiment/neutplatform/protodune']['rse_name']
                if parsed_url.scheme == 'gsiftp' and parsed_url.port == 2811:
                    #remove the default port
                    url_components = list(parsed_url)
                    url_components[1] = parsed_url.hostname
                    pfn = urlparse.urlunparse(url_components)

                print('DID %s:%s' % (scope, fi.file_name))
                print(' PFN: %s' % pfn)
                print(' size, adler32, md5: %s %s %s' % (fi.file_size, fi.adler32, fi.md5))

                file_info = { 'scope' : scope, 'name' : fi.file_name, 'bytes' : fi.file_size, 'pfn' : pfn }
                if fi.adler32 is not None:
                    file_info['adler32'] = fi.adler32
                if fi.md5 is not None:
                    file_info['md5'] = fi.md5
                replicas_to_add[rse].append( file_info )
                dids_to_add.add( ( scope, fi.file_name ) )
            
            if replicas_to_add:
                if not dry_run:

                    for rse in replicas_to_add:
                        # check for existing
                        print("rse,replicas to add")
                        print(rse)
                        print("knowndids")
                        print(knowndids)
                        possible_replicas = knowndids.intersection( (r["scope"], r["name"]) for r in replicas_to_add[rse])
                        if possible_replicas:
                            print("possible replicas")
                            print(possible_replicas)
                            existing_replicadata = list(root_replicaclient.list_replicas([{"scope":a[0], "name": a[1]} for a in possible_replicas], rse_expression=rse, all_states=True))
                            print("existing_replicadata")
                            print(existing_replicadata)
                            existing_replicas = set( (r["scope"], r["name"]) for r in existing_replicadata if r["rses"])
                            unavailable_replicas = set( (r["scope"], r["name"]) for r in existing_replicadata if "states" in r and r["states"].get(rse) == 'UNAVAILABLE' )
                            if unavailable_replicas:

                                # fix up a broken entries
                                # this isn't the most efficient way of doing this, but it should be rare
                                #replicas_to_update = []
                                #for r in replicas_to_add[rse]:
                                #    if (r["scope"], r["name"]) in unavailable_replicas:
                                #        replicas_to_update.append( r )
                                #        replicas_to_update[-1]["state"] = 'A'
                                #print rse, replicas_to_update
                                #root_replicaclient.update_replicas_states(rse, replicas_to_update[0:1] )

                                # remove unavailable replicas
                                for r in unavailable_replicas:
                                    print('Replica %s:%s is marked unavailable on %s' % (r[0], r[1], rse))
                                root_replicaclient.delete_replicas(rse, [{"scope": scope, "name": name} for scope, name in unavailable_replicas])
                                print('Removed %d unavailable replicas from %s' % (len(unavailable_replicas), rse))

                                # and readd them
                                existing_replicas -= unavailable_replicas

                            replicas_to_add[rse] = [ r for r in replicas_to_add[rse] if (r["scope"], r["name"]) not in existing_replicas ]
                        if replicas_to_add[rse]:
                            ret = root_replicaclient.add_replicas(rse, replicas_to_add[rse])
                            declared = declared if declared is not None else True and bool(ret)
                        if rse == 'CERN_PDUNE_EOS' and not eos_rule:
                            # flag so we create a rule at the end pinning this to EOS
                            # (defer to end to avoid a race where some files at FNAL haven't been added to EOS yet, so Rucio creates a placeholder)
                            eos_rule = True
                        if rse == 'CERN_PDUNE_CASTOR' and not castor_rule:
                            castor_rule = True
 #                       if rse == 'FNAL_DCACHE' and not fnal_rule:
 #                           fnal_rule = True
                        if rse == 'FNAL_DCACHE' and not fnal_rule:
                            fnal_rule = True
                        if rse == 'FNAL_MISC_TAPE' and not fnal_misc_tape_rule:
                            fnal_misc_tape_rule = True
                        if rse == 'FNAL_MISC_TAPE_USERS' and not fnal_misc_tape_users_rule:
                            fnal_misc_tape_users_rule= True
                        if rse == 'FNAL_MISC_TAPE_MC_BACKUP' and not fnal_misc_tape_mc_backup_rule:
                            fnal_misc_tape_mc_backup_rule = True
                        if rse == 'FNAL_MISC_TAPE_LBNE' and not fnal_misc_tape_lbne_rule:
                            fnal_misc_tape_lbne_rule = True
                    dids_to_add -= knowndids
                    if dids_to_add:
                        ret = didclient.attach_dids(scope, rucio_dataset, [{"scope":a[0], "name": a[1]} for a in dids_to_add])
                        knowndids.update(dids_to_add)
                        attached = bool(ret)
                    print("    --- declared: %s attached: %s" % (sum( len(rs) for rs in replicas_to_add.values()), len(dids_to_add)))
                else:
                    print("    --- would declare %s files" % ", ".join( "%s : %d" % (rse, len(flist)) for rse, flist in replicas_to_add.items()))
                    declared = True

                untagged_files = samweb.listFiles('file_name %s minus full_path "rucio:%s"' % ( ','.join(fi.file_name for fi in filelist), scope))
                for ut in untagged_files:
                    if not dry_run:
                        print("    --- added sam location rucio:%s to %s" % (scope, ut))
                        samweb.addFileLocation(ut, "rucio:%s" % scope)
                    else:
                        print("    --- would add sam location rucio:%s to %s" % (scope, ut))

        if not declared:
            print('No files declared')

        if found_files:
            if eos_rule:
                add_rule(ruleclient, rucio_dataset, 'CERN_PDUNE_EOS', rucio_account)
            if castor_rule:
                add_rule(ruleclient, rucio_dataset, 'CERN_PDUNE_CASTOR', rucio_account)
#            if fnal_rule:
#                add_rule(ruleclient, rucio_dataset, 'FNAL_DCACHE', rucio_account)
            if fnal_rule:
                add_rule(ruleclient, rucio_dataset, 'FNAL_DCACHE', rucio_account)
            if fnal_misc_tape_rule:
                add_rule(ruleclient, rucio_dataset, 'FNAL_MISC_TAPE', rucio_account)
            if fnal_misc_tape_users_rule:
                add_rule(ruleclient, rucio_dataset, 'FNAL_MISC_TAPE_USERS', rucio_account)
            if fnal_misc_tape_mc_backup_rule:
                add_rule(ruleclient, rucio_dataset, 'FNAL_MISC_TAPE_MC_BACKUP', rucio_account)
            if fnal_misc_tape_lbne_rule:
                add_rule(ruleclient, rucio_dataset, 'FNAL_MISC_TAPE_LBNE', rucio_account)

            containers = []
            if rucio_container and run_number not in filter_runs:
                containers.append(rucio_container)
            if stream_container and run_number not in filter_runs:
                if not data_stream and declared:
                    print('No stream available for run %s' % run_number)
                else:
                    c = stream_container.get(data_stream)
                    if c:
                        containers.append(c)


            for rucio_container in containers:
                if not dry_run:
                    print( scope, rucio_container )                    
                    try:
                        metadid="%s:%s" % (scope,rucio_container)
                        metads=metaclient.get_dataset(metadid)
                    except NotFoundError:
                        metaclient.create_dataset(metadid)
                    print (metads)
                    try:
                        didclient.add_container(scope, rucio_container)
                    except DataIdentifierAlreadyExists:
                        exists=True
                    except AccessDenied:
                        metaclient.create_dataset(metadid)
                        didclient.add_container(scope, rucio_container)
                    try:
                        didclient.add_datasets_to_container(scope, rucio_container, [{"scope": scope, "name": rucio_dataset}])
                        print('Added dataset %s:%s to container %s:%s' % (scope, rucio_dataset, scope, rucio_container))
                    except DuplicateContent:
                        print('Dataset %s:%s is already in container %s::%s' % (scope, rucio_dataset, scope, rucio_container))
                    except RucioException as ex:
                        if 'already exists' in ex.args[0][0]:
                            print('Dataset %s:%s is already in container %s::%s' % (scope, rucio_dataset, scope, rucio_container))
                        else:
                            raise
                else:
                    print('Would add dataset %s:%s to container %s:%s' % (scope, rucio_dataset, scope, rucio_container))

Usage = """
python mcruciosam_metacat.py -c <config.yaml> <data type> <run begin>-<run end>
"""

dry_run = Debug


if __name__ == '__main__':
    import yaml, getopt
    
    opts, args = getopt.getopt(sys.argv[1:], "c:")
    if not args:
        print(Usage)
        sys.exit(2)
        
    opts = dict(opts)
    data_type = args[0]
    if len(args)>=2:
        run_range = args[1].split('-')
        runlist = True
    else:
        run_range = [1]
        runlist = False
        
    config = yaml.load(open(opts["-c"], "r"), Loader=yaml.SafeLoader)
    #print("config:", config)
    pattern_re = pattern_match = dt_cfg = None
    for cfg in config["data_types"]:
        print(cfg["pattern"], data_type)
        pattern_re = re.compile(cfg["pattern"])
        pattern_match = pattern_re.match(data_type)   
        if pattern_match:
            print("match:", pattern_match, pattern_match.groups())
            dt_cfg = cfg
            break
    else:
        sys.exit('Unknown input data %s' % data_type)

    defaults = config["defaults"]

    def cfg(name):
        value = dt_cfg.get(name, defaults.get(name))
        if value == 'null':
            value = None
        if value == "$data_type":
            value = data_type
        return value

    filter_runs = cfg("filter_runs") or []
    if filter_runs: filter_runs = set(filter_runs)
    samdef = cfg("samdef")
    stream_container = cfg("stream_container") or {}
    rucio_container = cfg("rucio_container")
    scope = cfg("scope")
    if runlist == False:
        rucio_run_dataset=samdef
    else:
        rucio_run_dataset = cfg("rucio_run_dataset")
#$    rucio_run_dataset = pattern_re.sub(rucio_run_dataset, data_type)
    
    if Debug:
        print("filter_runs      :", filter_runs)
        print("samdef           :", samdef)
        print("stream_container :", stream_container)
        print("rucio_container  :", rucio_container)
        print("scope            :", scope)
        print("rucio_run_dataset:", rucio_run_dataset)
        print("runlist          :", runlist)
    
    

    rucio_account = 'dunepro'

    if not Debug:
        if len(run_range) == 1:
            add_run(samdef, int(run_range[0]), rucio_account, scope, rucio_run_dataset, rucio_container, runlist, filter_runs=filter_runs, stream_container=stream_container)
        elif len(run_range) == 2:
            if py3: xrange = range
            for run_number in xrange(int(run_range[0]), int(run_range[1])+1):
                add_run(samdef, run_number, rucio_account, scope, rucio_run_dataset, rucio_container, runlist, filter_runs=filter_runs, stream_container=stream_container)


