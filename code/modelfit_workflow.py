#!/usr/bin/env python3

"""
=========================
fMRI Workflow for NHP
=========================

See:

* https://github.com/nipy/nipype/blob/master/examples/fmri_fsl_reuse.py
* http://nipype.readthedocs.io/en/latest/users/examples/fmri_fsl_reuse.html
"""

from __future__ import print_function
from __future__ import division
from builtins import str
from builtins import range

import os                                     # system functions
import nipype.interfaces.io as nio            # Data i/o
import nipype.interfaces.fsl as fsl           # fsl
from nipype.interfaces import utility as niu  # Utilities
import nipype.pipeline.engine as pe           # pypeline engine
import nipype.algorithms.modelgen as model    # model generation
import nipype.algorithms.rapidart as ra       # artifact detection

import nipype.workflows.fmri.fsl as fslflows

import preprocessing_workflow as preproc
from filter_numbers import FilterNumsTask

from nipype import config, logging

ds_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
data_dir = ds_root

def create_workflow(contrasts, combine_runs=True):

    level1_workflow = pe.Workflow(name='level1flow')
    # ===================================================================
    #                  _____                   _
    #                 |_   _|                 | |
    #                   | |  _ __  _ __  _   _| |_
    #                   | | | '_ \| '_ \| | | | __|
    #                  _| |_| | | | |_) | |_| | |_
    #                 |_____|_| |_| .__/ \__,_|\__|
    #                             | |
    #                             |_|
    # ===================================================================

    # ------------------ Specify variables
    inputnode = pe.Node(niu.IdentityInterface(fields=[
        #'funcmasks',
        'fwhm',  # smoothing
        'highpass',

        'funcs',
        'event_log',
        'motion_parameters',
        'motion_outlier_files',

        'ref_func',
        'ref_funcmask',
    ]), name="inputspec")

    def remove_runs_missing_funcs(in_files, in_funcs):
        import os
        # import pdb
        import re

        # if input.synchronize = True, then in_files and in_funcs will
        #  be single strings
        assert not isinstance(in_files, str), "in_files must be list"
        assert not isinstance(in_funcs, str), "in_funcs must be list"

        if isinstance(in_files, str):
            in_files = [in_files]

        if isinstance(in_funcs, str):
            in_funcs = [in_funcs]

        has_func = set()
        for f in in_funcs:
            base = os.path.basename(f)
            try:
                sub = re.search(r'sub-([a-zA-Z0-9]+)_', base).group(1)
                ses = re.search(r'ses-([a-zA-Z0-9]+)_', base).group(1)
                run = re.search(r'run-([a-zA-Z0-9]+)_', base).group(1)
            except AttributeError as e:
                raise RuntimeError('Could not process "sub-*_", "ses-*_", or "run-*_" from func "%s"' % f)
            has_func.add((sub, ses, run)) 

        files = []
        for f in in_files:
            base = os.path.basename(f)
            try:
                sub = re.search(r'sub-([a-zA-Z0-9]+)_', base).group(1)
                ses = re.search(r'ses-([a-zA-Z0-9]+)_', base).group(1)
                run = re.search(r'run-([a-zA-Z0-9]+)_', base).group(1)
            except AttributeError as e:
                raise RuntimeError('Could not process "sub-*_", "ses-*_", or "run-*_" from event file "%s"' % f)
            if (sub, ses, run) in has_func:
                files.append(f)
        return files

    input_events = pe.Node(
        interface=niu.Function(input_names=['in_files', 'in_funcs'],
                           output_names=['out_files'],
                           function=remove_runs_missing_funcs),
        name='input_events',
    )

    level1_workflow.connect([
        (inputnode, input_events,
         [('funcs', 'in_funcs'),
          ('event_log', 'in_files'),
          ]),
    ])


    # -------------------------------------------------------------------
    #            /~_ _  _  _  _. _   _ . _  _ |. _  _
    #            \_/(/_| |(/_| |(_  |_)||_)(/_||| |(/_
    #                               |   |
    # -------------------------------------------------------------------
    """
    Preliminaries
    -------------
    Setup any package specific configuration. The output file format for FSL
    routines is being set to compressed NIFTI.
    """

    fsl.FSLCommand.set_default_output_type('NIFTI_GZ')

    modelfit = fslflows.create_modelfit_workflow()

    if combine_runs:
        fixed_fx = fslflows.create_fixed_effects_flow()
    else:
        fixed_fx = None

    """
    Artifact detection is done in preprocessing workflow.
    """

    """
    Add model specification nodes between the preprocessing and modelfitting
    workflows.
    """
    modelspec = pe.Node(model.SpecifyModel(),
                        name="modelspec")

    """
    Set up first-level workflow
    ---------------------------
    """


    def sort_copes(files):
        """ Sort by copes and the runs, ie. 
            [[cope1_run1, cope1_run2], [cope2_run1, cope2_run2]]
        """
        assert files[0] is not str

        numcopes = len(files[0])
        assert numcopes > 1

        outfiles = []
        for i in range(numcopes):
            outfiles.insert(i, [])
            for j, elements in enumerate(files):
                outfiles[i].append(elements[i])
        return outfiles


    def num_copes(files):
        return len(files)

    if fixed_fx is not None:
        level1_workflow.connect([
            (inputnode, fixed_fx,
             [('ref_funcmask', 'flameo.mask_file')]),  # To-do: use reference mask!!!
            (modelfit, fixed_fx,
            [(('outputspec.copes', sort_copes), 'inputspec.copes'),
            ('outputspec.dof_file', 'inputspec.dof_files'),
            (('outputspec.varcopes', sort_copes), 'inputspec.varcopes'),
            (('outputspec.copes', num_copes), 'l2model.num_copes'),
            ])
        ])

    # -------------------------------------------------------------------
    #          /~\  _|_ _   _|_
    #          \_/|_|| |_)|_||
    #                  |
    # -------------------------------------------------------------------
    # Datasink

    outputfiles = pe.Node(nio.DataSink(
        base_directory=ds_root,
        container='derivatives/modelfit',
        parameterization=True),
        name="output_files")

    # Use the following DataSink output substitutions
    outputfiles.inputs.substitutions = [
        ('subject_id_', 'sub-'),
        ('session_id_', 'ses-'),
        # ('/mask/', '/'),
        # ('_preproc_flirt_thresh.nii.gz', '_transformedmask.nii.gz'),
        # ('_preproc_volreg_unwarped.nii.gz', '_preproc.nii.gz'),
        # ('_preproc_flirt_unwarped.nii.gz', '_preproc-mask.nii.gz'),
        # ('/_mc_method_afni3dvolreg/', '/'),
        # ('/funcs/', '/'),
        # ('/funcmasks/', '/'),
        # ('preproc_volreg.nii.gz', 'preproc.nii.gz'),
        ('/_mc_method_afni3dAllinSlices/', '/'),
    ]
    # Put result into a BIDS-like format
    outputfiles.inputs.regexp_substitutions = [
        (r'_ses-([a-zA-Z0-9]+)_sub-([a-zA-Z0-9]+)', r'sub-\2/ses-\1'),

        # (r'/_addmean[0-9]+/', r'/func/'),
        # (r'/_dilatemask[0-9]+/', r'/func/'),
        # (r'/_funcbrain[0-9]+/', r'/func/'),
        # (r'/_maskfunc[0-9]+/', r'/func/'),
        # (r'/_mc[0-9]+/', r'/func/'),
        # (r'/_meanfunc[0-9]+/', r'/func/'),
        # (r'/_outliers[0-9]+/', r'/func/'),
        # (r'/_undistort_masks[0-9]+/', r'/func/'),
        # (r'/_undistort[0-9]+/', r'/func/'),
    ]
    level1_workflow.connect([
        (modelfit, outputfiles,
         [(('outputspec.copes', sort_copes), 'copes'),
          ('outputspec.dof_file', 'dof_files'),
          (('outputspec.varcopes', sort_copes), 'varcopes'),
          ]),
    ])
    if fixed_fx is not None:
        level1_workflow.connect([
            (fixed_fx, outputfiles,
             [('outputspec.res4d', 'fx.res4d'),
              ('outputspec.copes', 'fx.copes'),
              ('outputspec.varcopes', 'fx.varcopes'),
              ('outputspec.zstats', 'fx.zstats'),
              ('outputspec.tstats', 'fx.tstats'),
              ]),
        ])

    # -------------------------------------------------------------------
    #          (~   _  _  _. _ _  _  _ _|_   _ _  _  _. |`. _
    #          (_><|_)(/_| || | |(/_| | |   _\|_)(/_(_|~|~|(_
    #              |                          |
    # -------------------------------------------------------------------
    #    """
    #    Experiment specific components
    #    ------------------------------
    #    """

    """
    Use the get_node function to retrieve an internal node by name. Then set the
    iterables on this node to perform two different extents of smoothing.
    """

    featinput = level1_workflow.get_node('modelfit.inputspec')
    # featinput.iterables = ('fwhm', [5., 10.])
    featinput.inputs.fwhm = 2.0

    hpcutoff_s = 50.  # FWHM in seconds
    TR = 2.5
    hpcutoff_nvol = hpcutoff_s / 2.5  # FWHM in volumns

    # Use Python3 for processing. See code/requirements.txt for pip packages.
    featinput.inputs.highpass = hpcutoff_nvol / 2.355  # Gaussian: σ in volumes - (REMEMBER to run with Python 3)

    """
    Setup a function that returns subject-specific information about the
    experimental paradigm. This is used by the
    :class:`nipype.modelgen.SpecifyModel` to create the information necessary
    to generate an SPM design matrix. In this tutorial, the same paradigm was used
    for every participant. Other examples of this function are available in the
    `doc/examples` folder. Note: Python knowledge required here.
    """

    # from timeevents.curvetracing import calc_curvetracing_events
    from timeevents import process_time_events

    timeevents = pe.MapNode(
        interface=process_time_events,  # calc_curvetracing_events,
        iterfield=('event_log', 'in_nvols', 'TR'),
        name='timeevents')

    def get_nvols(funcs):
        import nibabel as nib
        nvols = []

        if isinstance(funcs, str):
            funcs = [funcs]

        for func in funcs:
            func_img = nib.load(func)
            header = func_img.header
            try:
                nvols.append(func_img.get_data().shape[3])
            except IndexError as e:
                # if shape only has 3 dimensions, then it is only 1 volume
                nvols.append(1)
        return(nvols)

    def get_TR(funcs):
        import nibabel as nib
        TRs = []

        if isinstance(funcs, str):
            funcs = [funcs]

        for func in funcs:
            func_img = nib.load(func)
            header = func_img.header
            try:
                TR = round(header.get_zooms()[3], 5)
            except IndexError as e:
                TR = 2.5
                print("Warning: %s did not have TR defined in the header. "
                      "Using default TR of %0.2f" %
                      (func, TR))

            assert TR > 1
            TRs.append(TR)
        return(TRs)

    level1_workflow.connect([
        (inputnode, timeevents,
         [(('funcs', get_nvols), 'in_nvols'),
          (('funcs', get_TR), 'TR'),
          ]),
        (input_events, timeevents,
         [('out_files', 'event_log')]),
        (inputnode, modelspec,
         [('motion_parameters', 'realignment_parameters')]),
        (modelspec, modelfit,
         [('session_info', 'inputspec.session_info')]),
    ])

    # Ignore volumes after last good response
    filter_outliers = pe.MapNode(
        interface=FilterNumsTask(),
        name='filter_outliers',
        iterfield=('in_file','max_number')
    )

    level1_workflow.connect([
        (inputnode, filter_outliers,
         [('motion_outlier_files', 'in_file')]),
        (filter_outliers, modelspec,
         [('out_file', 'outlier_files')]),
        (timeevents, filter_outliers,
         [('out_nvols', 'max_number')]),
    ])

    def evt_info(cond_events):
        output = []

        # for each run
        for ev in cond_events:
            from nipype.interfaces.base import Bunch
            from copy import deepcopy
            names = []

            for name in ev.keys():
                if ev[name].shape[0] > 0:
                    names.append(name)

            onsets = [deepcopy(ev[name].time) if ev[name].shape[0] > 0 else [] for name in names]
            durations = [deepcopy(ev[name].dur) if ev[name].shape[0] > 0 else [] for name in names]
            amplitudes = [deepcopy(ev[name].amplitude) if ev[name].shape[0] > 0 else [] for name in names]

            run_results = Bunch(
                conditions=names,
                onsets=[deepcopy(ev[name].time) for name in names],
                durations=[deepcopy(ev[name].dur) for name in names],
                amplitudes=[deepcopy(ev[name].amplitude) for name in names])

            output.append(run_results)
        return output

    modelspec.inputs.input_units = 'secs'
    modelspec.inputs.time_repetition = TR  # to-do: specify per func
    modelspec.inputs.high_pass_filter_cutoff = hpcutoff_s

    modelfit.inputs.inputspec.interscan_interval = TR  # to-do: specify per func
    modelfit.inputs.inputspec.bases = {'dgamma': {'derivs': True}}
    modelfit.inputs.inputspec.contrasts = contrasts
    modelfit.inputs.inputspec.model_serial_correlations = True
    modelfit.inputs.inputspec.film_threshold = 1000

    # level1_workflow.base_dir = os.path.abspath('./workingdirs/level1flow')
    modelfit.config['execution'] = dict(
        crashdump_dir=os.path.abspath('.'))

    # Ignore volumes after subject has finished working for the run
    beh_roi = pe.MapNode(
        fsl.ExtractROI(t_min=0),
        name='beh_roi',
        iterfield=['in_file', 't_size'])

    level1_workflow.connect([
        (timeevents, modelspec,
         [(('out_events', evt_info), 'subject_info'),
          ]),
        (inputnode, beh_roi,
         [('funcs', 'in_file'),
          ]),
        (timeevents, beh_roi,
         [('out_nvols', 't_size'),
          ]),
        (beh_roi, modelspec,
         [('roi_file', 'functional_runs'),
          ]),
        (beh_roi, modelfit,
         [('roi_file', 'inputspec.functional_data'),
          ]),
        (beh_roi, outputfiles,
         [('roi_file', 'roi_file'),
          ]),
        # (inputnode, datasource, [('in_data', 'base_directory')]),
        # (infosource, datasource, [('subject_id', 'subject_id')]),
        # (infosource, modelspec, [(('subject_id', subjectinfo), 'subject_info')]),
        # (datasource, preproc, [('func', 'inputspec.func')]),
    ])
    return(level1_workflow)
# ===================================================================
#                       ______ _
#                      |  ____(_)
#                      | |__   _ _ __
#                      |  __| | | '_ \
#                      | |    | | | | |
#                      |_|    |_|_| |_|
#
# ===================================================================


"""
Execute the pipeline
--------------------
The code discussed above sets up all the necessary data structures with
appropriate parameters and the connectivity between the processes, but does not
generate any output. To actually run the analysis on the data the
``nipype.pipeline.engine.Pipeline.Run`` function needs to be called.
"""

def run_workflow(csv_file, use_pbs, contrasts_name, template):
    workflow = pe.Workflow(name='run_level1flow')
    workflow.base_dir = os.path.abspath('./workingdirs')

    from nipype import config, logging
    config.update_config(
        {'logging':
         {'log_directory': os.path.join(workflow.base_dir, 'logs'),
          'log_to_file': True,
          'workflow_level': 'DEBUG',
          'interface_level': 'DEBUG',
          }})
    logging.update_logging(config)

    config.enable_debug_mode()

    # redundant with enable_debug_mode() ...
    workflow.stop_on_first_crash = True
    workflow.remove_unnecessary_outputs = False
    workflow.keep_inputs = True
    workflow.hash_method = 'content'

    """
    Setup the contrast structure that needs to be evaluated. This is a list of
    lists. The inner list specifies the contrasts and has the following format:
    [Name,Stat,[list of condition names],[weights on those conditions]. The
    condition names must match the `names` listed in the `evt_info` function
    described above.
    """

    try:
        import importlib
        mod = importlib.import_module('contrasts.' + contrasts_name)
        contrasts = mod.contrasts
        # event_names = mod.event_names
    except ImportError:
        raise RuntimeError('Unknown contrasts: %s. Must exist as a Python'
                           ' module in contrasts directory!' % contrasts_name)

    modelfit = create_workflow(contrasts)

    import bids_templates as bt

    inputnode = pe.Node(niu.IdentityInterface(fields=[
        'subject_id',
        'session_id',
        'run_id',
    ]), name='input')

    assert csv_file is not None, "--csv argument must be defined!"

    reader = niu.CSVReader()
    reader.inputs.header = True
    reader.inputs.in_file = csv_file
    out = reader.run()
    subject_list = out.outputs.subject
    session_list = out.outputs.session
    run_list = out.outputs.run

    inputnode.iterables = [
        ('subject_id', subject_list),
        ('session_id', session_list),
        ('run_id', run_list),
    ]
    inputnode.synchronize = True

    templates = {
        'funcs':
        'derivatives/featpreproc/highpassed_files/sub-{subject_id}/ses-{session_id}/func/'
            'sub-{subject_id}_ses-{session_id}_*_run-{run_id}*_bold_res-1x1x1_preproc_*.nii.gz',

        # 'funcmasks':
        # 'featpreproc/func_unwarp/sub-{subject_id}/ses-{session_id}/func/'
        #     'sub-{subject_id}_ses-{session_id}_*_run-{run_id}*_bold_res-1x1x1_preproc'
        #     '_mc_unwarped.nii.gz',

        'highpass':
        'derivatives/featpreproc/highpassed_files/sub-{subject_id}/ses-{session_id}/func/'
            'sub-{subject_id}_ses-{session_id}_*_run-{run_id}_bold_res-1x1x1_preproc_*.nii.gz',

        'motion_parameters':
        'derivatives/featpreproc/motion_corrected/sub-{subject_id}/ses-{session_id}/func/'
            'sub-{subject_id}_ses-{session_id}_*_run-{run_id}_bold_res-1x1x1_preproc.param.1D',

        'motion_outlier_files':
        'derivatives/featpreproc/motion_outliers/sub-{subject_id}/ses-{session_id}/func/'
            'art.sub-{subject_id}_ses-{session_id}_*_run-{run_id}_bold_res-1x1x1_preproc_mc'
            '_maths_outliers.txt',

        'event_log':
        'sub-{subject_id}/ses-{session_id}/func/'
            # 'sub-{subject_id}_ses-{session_id}*_bold_res-1x1x1_preproc'
            'sub-{subject_id}_ses-{session_id}*run-{run_id}*'
            # '.nii.gz',
            '_events.tsv',

        'ref_func':
        'derivatives/featpreproc/reference/func/*.nii.gz',

        'ref_funcmask':
        'derivatives/featpreproc/reference/func_mask/*.nii.gz',
    }

    inputfiles = pe.Node(
        nio.SelectFiles(templates,
                        base_directory=data_dir),
        name='in_files')

    workflow.connect([
        (inputnode, inputfiles,
         [('subject_id', 'subject_id'),
          ('session_id', 'session_id'),
          ('run_id', 'run_id'),
          ]),
    ])

    join_input = pe.JoinNode(
        niu.IdentityInterface(
            fields=[ 
                # 'subject_id',
                # 'session_id',
                # 'run_id',
                'funcs',
                'highpass',
                'motion_parameters',
                'motion_outlier_files',
                'event_log',
                'ref_func',
                'ref_funcmask',
            ]
            ),
            joinsource='input',
            joinfield=[
                'funcs',
                'highpass',
                'motion_parameters',
                'motion_outlier_files',
                'event_log',
            ],
            # unique=True,
            name='join_input')

    workflow.connect([
        (inputfiles, join_input,
         [
          ('funcs', 'funcs'),
          ('highpass', 'highpass'),
          ('motion_parameters', 'motion_parameters'),
          ('motion_outlier_files', 'motion_outlier_files'),
          ('event_log', 'event_log'),
          ('ref_func', 'ref_func'),
          ('ref_funcmask', 'ref_funcmask'),
          ]),
        (join_input, modelfit,
         [
          ('funcs', 'inputspec.funcs'),
          ('highpass', 'inputspec.highpass'),
          ('motion_parameters', 'inputspec.motion_parameters'),
          ('motion_outlier_files', 'inputspec.motion_outlier_files'),
          ('event_log', 'inputspec.event_log'), ('ref_func', 'inputspec.ref_func'),
          ('ref_funcmask', 'inputspec.ref_funcmask'),
          ]),
    ])

    modelfit.inputs.inputspec.fwhm = 2.0
    modelfit.inputs.inputspec.highpass = 50
    modelfit.write_graph(simple_form=True)
    modelfit.write_graph(graph2use='orig', format='png', simple_form=True)
    # modelfit.write_graph(graph2use='detailed', format='png', simple_form=False)

    workflow.stop_on_first_crash = True
    workflow.keep_inputs = True
    workflow.remove_unnecessary_outputs = False
    workflow.write_graph(simple_form=True)
    workflow.write_graph(graph2use='colored', format='png', simple_form=True)
    # workflow.write_graph(graph2use='detailed', format='png', simple_form=False)
    if use_pbs:
        workflow.run(plugin='PBS', plugin_args={'template':
                                                os.path.expanduser(template)})
    else:
        workflow.run()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
            description='Analyze model fit.')
    parser.add_argument('--csv', dest='csv_file', required=True,
                        help='CSV file with subjects, sessions, and runs.')
    parser.add_argument('--contrasts', dest='contrasts_name', required=True,
                        help='Contrasts to use.')
    parser.add_argument('--pbs', dest='use_pbs', action='store_true',
                        help='Whether to use pbs plugin.')
    parser.add_argument('--template', default='~/NHP-BIDS/pbs-template.sh',
                        help='PBS template')
    args = parser.parse_args()
    run_workflow(**vars(args))
