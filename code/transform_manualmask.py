#!/usr/bin/env python3

# http://nipype.readthedocs.io/en/latest/users/examples/fmri_fsl.html
# http://miykael.github.io/nipype-beginner-s-guide/firstSteps.html#input-output-stream
import os                                    # system functions

import nipype.interfaces.io as nio           # Data i/o
import nipype.interfaces.utility as util     # utility
import nipype.pipeline.engine as pe          # pypeline engine
import nipype.algorithms.modelgen as model   # model generation
import nipype.algorithms.rapidart as ra      # artifact detection
import nipype.interfaces.fsl as fsl          # fsl
from nipype.interfaces.utility import IdentityInterface

from nipype.pipeline.engine import Workflow, Node, MapNode

from nipype import config
config.enable_debug_mode()

from nipype.interfaces.base import File


class ApplyXFMInputSpecRefName(fsl.preprocess.ApplyXFMInputSpec):
    out_file = File(argstr='-out %s', desc='registered output file',
                    name_source=['reference'], name_template='%s_flirt',
                    position=2, hash_files=False)


class ApplyXFMRefName(fsl.FLIRT):
    """Currently just a light wrapper around FLIRT,
    with no modifications
    ApplyXFMRefName uses the reference for naming the functional mask.
    """
    input_spec = ApplyXFMInputSpecRefName

ds_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def create_workflow():
    workflow = Workflow(
        name='transform_manual_mask')

    inputs = Node(IdentityInterface(fields=[
        'subject_id',
        'session_id',
        'manualmask',
        'manualmask_func_ref',
        'funcs',
    ]), name='in')

    # Find the transformation matrix func_ref -> func
    # First find transform from func to manualmask's ref func
    findtrans = MapNode(fsl.FLIRT(),
                        iterfield=['in_file'],
                        name='findtrans'
                        )

    # Invert the matrix transform
    invert = MapNode(fsl.ConvertXFM(invert_xfm=True),
                     name='invert',
                     iterfield=['in_file'],
                     )
    workflow.connect(findtrans, 'out_matrix_file',
                     invert, 'in_file')

    # Transform the manualmask to be aligned with func
    funcreg = MapNode(ApplyXFMRefName(),
                      name='funcreg',
                      iterfield=['in_matrix_file', 'reference'],
                      )

    workflow.connect(inputs, 'funcs',
                     findtrans, 'in_file')
    workflow.connect(inputs, 'manualmask_func_ref',
                     findtrans, 'reference')

    workflow.connect(invert, 'out_file',
                     funcreg, 'in_matrix_file')

    workflow.connect(inputs, 'manualmask',
                     funcreg, 'in_file')
    workflow.connect(inputs, 'funcs',
                     funcreg, 'reference')

    return workflow


def run_workflow():
    # ------------------ Specify variables
    subject_list = ['eddy']
    session_list = ['20170511']

    # ------------------ Input Files
    infosource = Node(IdentityInterface(fields=[
        'subject_id',
        'session_id',
    ]), name="infosource")

    infosource.iterables = [
        ('session_id', session_list),
        ('subject_id', subject_list),
    ]
    # SelectFiles
    templates = {
        'manualmask':
        'manual-masks/sub-eddy/ses-20170511/func/'
            'sub-eddy_ses-20170511_task-curvetracing_run-01_frame-50_bold'
            '_res-1x1x1_manualmask.nii.gz',

        'manualmask_func_ref':
        'manual-masks/sub-eddy/ses-20170511/func/'
            'sub-eddy_ses-20170511_task-curvetracing_run-01_frame-50_bold'
            '_res-1x1x1_reference.nii.gz',

        'funcs':
        'resampled-isotropic-1mm/sub-{subject_id}/ses-{session_id}/func/'
            'sub-{subject_id}_ses-{session_id}*_bold_res-1x1x1_preproc'
            '.nii.gz',
    }

    data_dir = ds_root
    output_dir = 'transformed-manual-func-mask'

    inputfiles = Node(
        nio.SelectFiles(templates,
                        base_directory=data_dir), name="input_files")

    # ------------------ Output Files
    # Datasink
    outputfiles = Node(nio.DataSink(
        base_directory=ds_root,
        container=output_dir,
        parameterization=True),
        name="output_files")

    # Use the following DataSink output substitutions
    outputfiles.inputs.substitutions = [
        ('subject_id_', 'sub-'),
        ('session_id_', 'ses-'),
        ('/mask/', '/'),
        ('_preproc_flirt_thresh.nii.gz', '_transformedmask.nii.gz'),
        #   ('/_findtrans0/', '/fmap/'),
        #   ('_magnitude1_res-1x1x1_manualmask_flirt',
        #    '_magnitude1_res-1x1x1_preproc'),
        # BIDS Extension Proposal: BEP003
        # ('_resample.nii.gz', '_res-1x1x1_preproc.nii.gz'),
        # remove subdirectories:
        # ('resampled-isotropic-1mm/isoxfm-1mm', 'resampled-isotropic-1mm'),
        # ('resampled-isotropic-1mm/mriconv-1mm', 'resampled-isotropic-1mm'),
    ]
    # Put result into a BIDS-like format
    outputfiles.inputs.regexp_substitutions = [
        (r'_ses-([a-zA-Z0-9]*)_sub-([a-zA-Z0-9]*)', r'sub-\2/ses-\1'),
        (r'_maskthresh[0-9]*/', r'func/'),
    ]

    # -------------------------------------------- Wrapper workflow
    working_dir = 'workingdirs/transform_manual_func_mask'

    wrapper = Workflow(
        name='transform_manual_func_mask',
        base_dir=os.path.join(ds_root, working_dir))

    # -------------------------------------------- Create Pipeline
    workflow = create_workflow()

    wrapper.connect([(infosource, inputfiles,
                      [('subject_id', 'subject_id'),
                       ('session_id', 'session_id'),
                       ])])

    wrapper.connect(inputfiles, 'manualmask',
                    workflow, 'in.manualmask')
    wrapper.connect(inputfiles, 'funcs',
                    workflow, 'in.funcs')
    wrapper.connect(inputfiles, 'manualmask_func_ref',
                    workflow, 'in.manualmask_func_ref')

    wrapper.stop_on_first_crash = True
    wrapper.keep_inputs = True
    wrapper.remove_unnecessary_outputs = False
    wrapper.write_graph()
    wrapper.run()

if __name__ == '__main__':
    run_workflow()
