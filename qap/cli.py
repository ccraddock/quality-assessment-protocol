#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Date:   2015-10-16 12:52:35
# @Last Modified by:   oesteban
# @Last Modified time: 2015-10-16 15:05:27
import os
import os.path as op
import time
import argparse
import yaml

from nipype import logging
logger = logging.getLogger('workflow')


class QAProtocolCLI:
    """
    This class and the associated _build_workflow function implement what
    the former scripts (qap_anatomical_spatial.py, etc.) contained
    """

    def __init__(self):
        parser = argparse.ArgumentParser()

        group = parser.add_argument_group(
            "Regular Use Inputs (non-cloud runs)")
        cloudgroup = parser.add_argument_group(
            "AWS Cloud Inputs (only required for AWS Cloud runs)")
        req = parser.add_argument_group("Required Inputs")

        cloudgroup.add_argument('--subj_idx', type=int,
                                help='Subject index to run')
        cloudgroup.add_argument(
            '--s3_dict_yml', type=str,
            help='Path to YAML file containing S3 input filepaths dictionary')

        # Subject list (YAML file)
        group.add_argument(
            "--sublist", type=str, help="filepath to subject list YAML")
        req.add_argument(
            "config", type=str, help="filepath to pipeline configuration YAML")

        args = parser.parse_args()

        # checks
        if args.subj_idx and not args.s3_dict_yml and not args.sublist:
            raise RuntimeError(
                "\n[!] You provided --subj_idx, but not --s3_dict_yml. "
                "When executing cloud-based runs, please provide both "
                "inputs.\n")

        elif args.s3_dict_yml and not args.subj_idx and not args.sublist:
            raise RuntimeError(
                "\n[!] You provided --s3_dict_yml, but not --subj_idx. "
                "When executing cloud-based runs, please provide both "
                "inputs.\n")

        elif not args.sublist and not args.subj_idx and not args.s3_dict_yml:
            raise RuntimeError(
                "\n[!] Either --sublist is required for regular runs, or "
                "both --subj_idx and --s3_dict_yml for cloud-based runs.\n")

        elif args.sublist and args.subj_idx and args.s3_dict_yml:
            raise RuntimeError(
                "\n[!] Either --sublist is required for regular runs, or "
                "both --subj_idx and --s3_dict_yml for cloud-based runs, "
                "but not all three. (I'm not sure which you are trying to "
                "do!)\n")

        elif args.sublist and (args.subj_idx or args.s3_dict_yml):
            raise RuntimeError(
                "\n[!] Either --sublist is required for regular runs, or "
                "both --subj_idx and --s3_dict_yml for cloud-based runs. "
                "(I'm not sure which you are trying to do!)\n")

        self._cloudify = False

        if args.subj_idx and args.s3_dict_yml:
            self._cloudify = True

            # ---- Cloud-ify! ----
            # Import packages
            from cloud_utils import dl_subj_from_s3, upl_qap_output
            # Download and build a one-subject dictionary from S3
            self._sub_dict = dl_subj_from_s3(
                args.subj_idx, args.config, args.s3_dict_yml)

            if not self._sub_dict:
                err = "\n[!] Subject dictionary was not successfully " \
                      "downloaded from the S3 bucket!\n"
                raise RuntimeError(err)

        elif args.sublist:
            self._sub_dict = args.sublist

        else:
            raise RuntimeError(
                "\n[!] Arguments were parsed, but no appropriate run found")

        # Load config
        with open(args.config, "r") as f:
            self._config = yaml.load(f)

        self._config['pipeline_config_yaml'] = args.config
        self._config['qap_type'] = parser.prog[4:-3]

    def _run_here(self, run_name):
        ns_at_once = self._config.get('num_subjects_at_once', 1)
        with open(self._sub_dict, "r") as f:
            subdict = yaml.load(f)

        flat_sub_dict = {}
        sites_dict = {}

        # Preamble: generate flat_subdict
        for subid in subdict.keys():
            # sessions
            for session in subdict[subid].keys():
                # resource files
                for resource in subdict[subid][session].keys():
                    if type(subdict[subid][session][resource]) is dict:
                        # then this has sub-scans defined
                        for scan in subdict[subid][session][resource].keys():
                            filepath = subdict[subid][session][resource][scan]
                            resource_dict = {}
                            resource_dict[resource] = filepath
                            sub_info_tuple = (subid, session, scan)
                            if sub_info_tuple not in flat_sub_dict.keys():
                                flat_sub_dict[sub_info_tuple] = {}

                            flat_sub_dict[sub_info_tuple].update(resource_dict)

                    elif resource == "site_name":
                        sites_dict[subid] = subdict[subid][session][resource]

                    else:
                        filepath = subdict[subid][session][resource]
                        resource_dict = {}
                        resource_dict[resource] = filepath
                        sub_info_tuple = (subid, session, None)

                        if sub_info_tuple not in flat_sub_dict.keys():
                            flat_sub_dict[sub_info_tuple] = {}

                        flat_sub_dict[sub_info_tuple].update(resource_dict)

            # in case some subjects have site names and others don't
            if len(sites_dict.keys()) > 0:
                for subid in subdict.keys():
                    if subid not in sites_dict.keys():
                        sites_dict[subid] = None

        # Start the magic
        logger.info('There are %d subjects in the pool' %
                    len(flat_sub_dict.keys()))

        # skip parallel machinery if we are running only one subject at once
        if ns_at_once == 1:
            for sub_info in flat_sub_dict.keys():
                _build_workflow(
                    flat_sub_dict[sub_info], self._config, sub_info,
                    run_name, sites_dict.get(sub_info[0], None))
        else:
            from multiprocessing import Process
            procss = [Process(
                target=_build_workflow,
                args=(flat_sub_dict[sub_info], self._config, sub_info,
                      run_name, sites_dict.get(sub_info[0], None)))
                      for sub_info in flat_sub_dict.keys()]
            pid = open(op.join(
                self._config["output_directory"], 'pid.txt'), 'w')
            # Init job queue

            job_queue = []
            # Stream the subject workflows for preprocessing.
            # At Any time in the pipeline c.numSubjectsAtOnce
            # will run, unless the number remaining is less than
            # the value of the parameter stated above
            idx = 0
            nprocs = len(procss)
            while idx < nprocs:
                # Check every job in the queue's status
                for job in job_queue:
                    # If the job is not alive
                    if not job.is_alive():
                        # Find job and delete it from queue
                        logger.info('found dead job: %s' % str(job))
                        loc = job_queue.index(job)
                        del job_queue[loc]
                # Check free slots after prunning jobs
                slots = ns_at_once - len(job_queue)
                if slots > 0:
                    idc = idx
                    for p in procss[idc:idc + slots]:
                        # ..and start the next available process
                        p.start()
                        print >>pid, p.pid
                        # Append this to job queue and increment index
                        job_queue.append(p)
                        idx += 1
                # Add sleep so while loop isn't consuming 100% of CPU
                time.sleep(2)
            pid.close()

    def _run_cloud(self, run_name):
        from cloud_utils import upl_qap_output
        # get the site name!
        for resource_path in subject_list[sub]:
            if ".nii" in resource_path:
                filepath = resource_path
                break

        filesplit = filepath.split(self._config["bucket_prefix"])
        site_name = filesplit[1].split("/")[1]

        _build_workflow(
            subject_list[sub], self._config, sub, run_name, site_name)

        # upload results
        upl_qap_output(self._config)

    def run(self):
        # Get configurations and settings
        config = self._config
        subject_list = self._sub_dict
        cloudify = self._cloudify
        ns_at_once = config.get('num_subjects_at_once', 1)

        # Create output directory
        try:
            os.makedirs(config["output_directory"])
        except:
            if not op.isdir(config["output_directory"]):
                err = "[!] Output directory unable to be created.\n" \
                      "Path: %s\n\n" % config["output_directory"]
                raise Exception(err)
            else:
                pass

        # Create working directory
        try:
            os.makedirs(config["working_directory"])
        except:
            if not op.isdir(config["working_directory"]):
                err = "[!] Output directory unable to be created.\n" \
                      "Path: %s\n\n" % config["working_directory"]
                raise Exception(err)
            else:
                pass

        run_name = config['pipeline_config_yaml'].split("/")[-1].split(".")[0]
        if not cloudify:
            self._run_here(run_name)
        else:
            self._run_cloud(run_name)


def _build_workflow(
        resource_pool, config, subject_info, run_name, site_name=None):

    # build pipeline for each subject, individually
    # ~ 5 min 20 sec per subject
    # (roughly 320 seconds)

    import os
    import os.path as op
    import sys

    import nipype.interfaces.io as nio
    import nipype.pipeline.engine as pe

    import nipype.interfaces.utility as util
    import nipype.interfaces.fsl.maths as fsl

    import glob

    import time
    from time import strftime
    from nipype import config as nyconfig

    sub_id = str(subject_info[0])

    qap_type = config['qap_type']

    if subject_info[1]:
        session_id = subject_info[1]
    else:
        session_id = "session_0"

    if subject_info[2]:
        scan_id = subject_info[2]
    else:
        scan_id = "scan_0"

    # Read and apply general settings in config
    keep_outputs = config.get('write_all_outputs', False)
    output_dir = op.join(config["output_directory"], run_name,
                         sub_id, session_id, scan_id)

    try:
        os.makedirs(output_dir)
    except:
        if not op.isdir(output_dir):
            err = "[!] Output directory unable to be created.\n" \
                  "Path: %s\n\n" % output_dir
            raise Exception(err)
        else:
            pass

    log_dir = output_dir

    # set up logging
    nyconfig.update_config(
        {'logging': {'log_directory': log_dir, 'log_to_file': True}})
    logging.update_logging(nyconfig)

    # take date+time stamp for run identification purposes
    unique_pipeline_id = strftime("%Y%m%d%H%M%S")
    pipeline_start_stamp = strftime("%Y-%m-%d_%H:%M:%S")

    pipeline_start_time = time.time()

    logger.info("Pipeline start time: %s" % pipeline_start_stamp)
    logger.info("Contents of resource pool:\n" + str(resource_pool))
    logger.info("Configuration settings:\n" + str(config))

    # for QAP spreadsheet generation only
    config.update({"subject_id": sub_id, "session_id": session_id,
                   "scan_id": scan_id, "run_name": run_name})

    if site_name:
        config["site_name"] = site_name

    workflow = pe.Workflow(name=scan_id)
    workflow.base_dir = op.join(config["working_directory"], sub_id,
                                session_id)

    # set up crash directory
    workflow.config['execution'] = \
        {'crashdump_dir': config["output_directory"]}

    # update that resource pool with what's already in the output directory
    for resource in os.listdir(output_dir):
        if (op.isdir(op.join(output_dir, resource)) and
                resource not in resource_pool.keys()):
            resource_pool[resource] = glob.glob(op.join(output_dir,
                                                        resource, "*"))[0]

    # resource pool check
    invalid_paths = []

    for resource in resource_pool.keys():
        if not op.isfile(resource_pool[resource]):
            invalid_paths.append((resource, resource_pool[resource]))

    if len(invalid_paths) > 0:
        err = "\n\n[!] The paths provided in the subject list to the " \
              "following resources are not valid:\n"

        for path_tuple in invalid_paths:
            err = err + path_tuple[0] + ": " + path_tuple[1] + "\n"

        err = err + "\n\n"
        raise Exception(err)

    # start connecting the pipeline
    if 'qap_' + qap_type not in resource_pool.keys():
        from qap import qap_workflows as qw
        wf_builder = getattr(qw, 'qap_' + qap_type + '_workflow')
        workflow, resource_pool = wf_builder(workflow, resource_pool, config)

    # set up the datasinks
    new_outputs = 0

    out_list = ['qap_' + qap_type]

    if keep_outputs:
        out_list = resource_pool.keys()

    for output in out_list:
        # we use a check for len()==2 here to select those items in the
        # resource pool which are tuples of (node, node_output), instead
        # of the items which are straight paths to files

        # resource pool items which are in the tuple format are the
        # outputs that have been created in this workflow because they
        # were not present in the subject list YML (the starting resource
        # pool) and had to be generated
        if len(resource_pool[output]) == 2:
            ds = pe.Node(nio.DataSink(), name='datasink_%s' % output)
            ds.inputs.base_directory = output_dir
            node, out_file = resource_pool[output]
            workflow.connect(node, out_file, ds, output)
            new_outputs += 1

    # run the pipeline (if there is anything to do)
    if new_outputs > 0:
        workflow.write_graph(
            dotfilename=op.join(output_dir, run_name + ".dot"),
            simple_form=False)
        if config["num_cores_per_subject"] == 1:
            workflow.run(plugin='Linear')
        else:
            workflow.run(
                plugin='MultiProc',
                plugin_args={'n_procs': config["num_cores_per_subject"]})

    else:
        print "\nEverything is already done for subject %s." % sub_id

    # Remove working directory when done
    if not keep_outputs:
        try:
            work_dir = op.join(workflow.base_dir, scan_id)

            if op.exists(work_dir):
                import shutil
                shutil.rmtree(work_dir)
        except:
            print "Couldn\'t remove the working directory!"
            pass

    pipeline_end_stamp = strftime("%Y-%m-%d_%H:%M:%S")
    pipeline_end_time = time.time()
    logger.info("Elapsed time (minutes) since last start: %s"
                % ((pipeline_end_time - pipeline_start_time) / 60))
    logger.info("Pipeline end time: %s" % pipeline_end_stamp)
    return workflow
