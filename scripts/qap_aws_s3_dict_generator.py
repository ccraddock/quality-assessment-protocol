def pull_S3_sublist(yaml_outpath, img_type, bucket_name, bucket_prefix, \
                        creds_path, session_list=None, series_list=None, \
                        BIDS=False):

    import os
    import yaml
    from indi_aws import fetch_creds

    s3_list = []
    s3_dict = {}

    bucket = fetch_creds.return_bucket(creds_path, bucket_name)

    # Filter for anat/rest
    if img_type == 'anat':
        subkey_type = 'anatomical_scan'
    elif img_type == 'func':
        subkey_type = 'functional_scan'


    # Build S3-subjects to download
    for bk in bucket.objects.filter(Prefix=bucket_prefix):
        s3_list.append(str(bk.key))


    # Read in series_list, if it is provided
    if session_list:
        try:
            session_list = os.path.abspath(session_list)
            with open(session_list,"r") as f:
                sessions = f.readlines()
            sessions = [i.rstrip("\n") for i in sessions]
        except:
            err = "\n\nCould not successfully read the session list.\n%s" \
                  % session_list
            raise Exception(err)

    # Read in series_list, if it is provided
    if series_list:
        try:
            series_list = os.path.abspath(series_list)
            with open(series_list,"r") as f:
                series = f.readlines()
            series = [i.rstrip("\n") for i in series]
        except:
            err = "\n\nCould not successfully read the series list.\n%s" \
                  % series_list
            raise Exception(err)


    # Build dictionary of filepaths
    for sfile in s3_list:

        include = False

        if BIDS:
            ssplit = sfile.split("/")
            folder = ssplit[-5]
            sub_id = ssplit[-4]
            session_id = ssplit[-3]
            scan_type = ssplit[-2]
            filename = ssplit[-1]

            if ".nii" not in filename:
                continue

            scan_id = None

            if sub_id in filename:
                scan_id = filename.replace(sub_id,"")
            if session_id in scan_id:
                scan_id = scan_id.replace(session_id,"")
            if ".nii" in scan_id:
                scan_id = scan_id.replace(".nii","")
            if ".gz" in scan_id:
                scan_id = scan_id.replace(".gz","")
            if "task-" in scan_id:
                scan_id = scan_id.replace("task-","")
            if "__" in scan_id:
                scan_id = scan_id.replace("__","")          

            if (img_type == "anat") and (scan_type == "anat"):
                # this requirement is subject to change with the BIDS spec!!
                if "T1w.nii" in filename:
                    include = True
            if (img_type == "func") and (scan_type == "func"):
                include = True

            if ("sub-" not in sub_id) or (scan_id == None):
                err = "\n\n[!] This is not a BIDS-formatted dataset!\n\n"
                raise Exception(err)

            # this is to get the folder containing the sub-ID folders so that
            # we can avoid sub-folders within the directory
            bucket_prefix_split = bucket_prefix.split("/")
            if bucket_prefix_split[-1] != "":
                containing_folder = bucket_prefix_split[-1]
            else:
                containing_folder = bucket_prefix_split[-2]

            if containing_folder != folder:
                include = False

            if session_list:
                if session_id not in sessions:
                    include = False

            if series_list:
                for series_id in series:
                    if series_id in scan_id:
                        break
                else:
                    include = False 


        else:
            ssplit = sfile.split('/')
            sub_id = ssplit[-4]
            session_id = ssplit[-3]
            scan_id = ssplit[-2]
            filename = ssplit[-1]

            if img_type == "anat":
                if ("anat" in scan_id) or ("anat" in filename) or \
                    ("mprage" in filename):
                    include = True
            if img_type == "func":
                if ("func" in scan_id) or ("rest" in scan_id) or \
                    ("func" in filename) or ("rest" in filename):
                    include = True

            if session_list:
                if session_id not in sessions:
                    include = False

            if series_list:
                if scan_id not in series:
                    include = False

        if (include == True) and ("nii" in filename):
        
            resource_dict = {}
            resource_dict[subkey_type] = sfile

            # this ONLY handles raw data inputs, not CPAC-generated outputs!
            if not s3_dict.has_key((sub_id, session_id, scan_id)):

                s3_dict[(sub_id, session_id, scan_id)] = {}
                s3_dict[(sub_id, session_id, scan_id)].update(resource_dict)

            else:

                s3_dict[(sub_id, session_id, scan_id)].update(resource_dict)    

        else:

            continue
    
            
    if len(s3_dict) == 0:
        err = "\n[!] Filepaths have not been successfully gathered from " \
              "the S3 bucket!\n"
        raise Exception(err)

    dict_len = len(s3_dict)            
           
    # write yaml file
    try:
        with open(yaml_outpath,"wt") as f:
            f.write(yaml.dump(s3_dict))
    except:
        err = "\n\n[!] Error writing YAML file output.\n1. Do you have " \
              "write permissions for the output path provided?\n2. Did you " \
              "provide a full path for the output path? Example: /home/data" \
              "/sublist.yml\n\nOutput path provided: %s\n\n" % yaml_outpath
        raise Exception(err)
        
    
    if os.path.isfile(yaml_outpath):
        print "\nS3 dictionary file successfully created: %s\n" % yaml_outpath
        print "Total number of subject-session-scans: %d\n" % dict_len
    else:
        err = "\n[!] Filepaths from the S3 bucket have not been " \
              "successfully saved to the YAML file!\nOutput filepath: %s\n" \
              % yaml_outpath
        raise Exception(err)



def main():

    import argparse

    parser = argparse.ArgumentParser()
                       
    parser.add_argument("scan_type", type=str, \
                            help="'anat' or 'func', depending on which QAP " \
                                 "measures you will be using the S3 subject "\
                                 "dictionary for")
 
    parser.add_argument("bucket_name", type=str, \
                            help="the name of your AWS S3 bucket")
 
    parser.add_argument("bucket_prefix", type=str, \
                            help="the filepath prefix to the top level of " \
                                 "your raw data directory on S3 storage")
 
    parser.add_argument("creds_path", type=str, \
                            help="the path to the file containing your AWS " \
                                 "credentials")

    parser.add_argument("outfile_path", type=str, \
                            help="the full filepath for the S3 subject " \
                                 "YAML dictionary this script will create")

    parser.add_argument('--include_sites', action='store_true', \
                            help="include this flag if you wish to include " \
                                 "site information in your subject list - " \
                                 "data must be organized as /site_name/" \
                                 "subject_id/session_id/scan_id/..")

    parser.add_argument("--session_list", type=str, \
                            help="filepath to a text file containing the " \
                                 "names of sessions you want included, one " \
                                 "on each line")

    parser.add_argument("--series_list", type=str, \
                            help="filepath to a text file containing the " \
                                 "names of series you want included, one " \
                                 "on each line")

    parser.add_argument("--BIDS", action="store_true", \
                            help="if the dataset is in BIDS format")
 
    args = parser.parse_args()


    # run it!
    pull_S3_sublist(args.outfile_path, args.scan_type, args.bucket_name, \
                        args.bucket_prefix, args.creds_path, \
                        args.session_list, args.series_list, args.BIDS)



if __name__ == "__main__":
    main()
    
    
