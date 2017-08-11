#!/usr/bin/env python3

# http://nipype.readthedocs.io/en/latest/users/examples/fmri_fsl.html
# http://miykael.github.io/nipype-beginner-s-guide/firstSteps.html#input-output-stream
import os                                    # system functions

import nipype.interfaces.io as nio           # Data i/o
import nipype.interfaces.fsl as fsl          # fsl
import nipype.interfaces.utility as util     # utility
import nipype.pipeline.engine as pe          # pypeline engine
import nipype.algorithms.modelgen as model   # model generation
import nipype.algorithms.rapidart as ra      # artifact detection
from nipype.interfaces.utility import IdentityInterface

from nipype.interfaces.fsl.preprocess import PRELUDE
from nipype.interfaces.fsl.preprocess import FUGUE
from nipype.interfaces.fsl.preprocess import BET

from nipype.pipeline.engine import Workflow, Node, MapNode, JoinNode

from nipype import config

# This pipeline depends on transform_manual_fmap_mask


def run_workflow():
    config.enable_debug_mode()

    # ------------------ Specify variables
    ds_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

    data_dir = ds_root
    output_dir = 'func_unwarp'
    working_dir = 'workingdirs/func_unwarp'

    subject_list = ['eddy']
    session_list = ['20170511']

    # ------------------ Input Files
    infosource = Node(IdentityInterface(fields=[
        'subject_id',
        'session_id',
        'unwarp_direction'
    ]), name="infosource")

    infosource.iterables = [
        ('session_id', session_list),
        ('subject_id', subject_list),
        ('unwarp_direction', ['y']),
    ]
    # SelectFiles
    templates = {
        'func':
        'resampled-isotropic-1mm/sub-{subject_id}/ses-{session_id}/func/'
            'sub-{subject_id}_ses-{session_id}_'
            'task-*_bold_res-1x1x1_preproc.nii.gz',

        # The manual mask and reference
        'func_manmask':
        'manual-masks/sub-eddy/ses-20170511/func/'
            'sub-eddy_ses-20170511_task-curvetracing_'
            'run-01_frame-50_bold_res-1x1x1_*.nii.gz',
        # Use *-roi for testing
        #    'task-curvetracing_run-01_bold_res-1x1x1_preproc-roi.nii.gz',

        'fmap_phasediff':
        'resampled-isotropic-1mm/sub-{subject_id}/ses-{session_id}/fmap/'
            'sub-{subject_id}_ses-{session_id}_phasediff_res-1x1x1_preproc'
            '.nii.gz',

        'fmap_magnitude':
        'resampled-isotropic-1mm/sub-{subject_id}/ses-{session_id}/fmap/'
            'sub-{subject_id}_ses-{session_id}_magnitude1_res-1x1x1_preproc'
            '.nii.gz',

        'fmap_mask':
        'transformed-manual-fmap-mask/sub-{subject_id}/ses-{session_id}/fmap/'
            'sub-{subject_id}_ses-{session_id}_'
            'magnitude1_res-1x1x1_preproc.nii.gz',
    }
    inputfiles = Node(
        nio.SelectFiles(
            templates, base_directory=data_dir), name="input_files")

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
        ('/undistorted/', '/'),
        ('/undistorted_manmasks/', '/'),
        ('_unwarped.nii.gz', '.nii.gz'),
        ('_unwarp_direction_y', ''),
        ('phasediff_radians_unwrapped_mask', '_rec-unwrapped_phasediff'),
    ]
    outputfiles.inputs.regexp_substitutions = [
        (r'_fugue[0-9]+/', r'func/'),
        (r'_undistort_manmasks[0-9]+/', r'func/'),
        (r'_ses-([a-zA-Z0-9]*)_sub-([a-zA-Z0-9]*)', r'sub-\2/ses-\1')]

    # -------------------------------------------- Create Pipeline

    workflow = Workflow(
        name='undistort',
        base_dir=os.path.join(ds_root, working_dir))

    workflow.connect([(infosource, inputfiles,
                      [('subject_id', 'subject_id'),
                       ('session_id', 'session_id')])])

    # --- --- --- --- --- --- --- Convert to radians --- --- --- --- --- ---

    # fslmaths $FUNCDIR/"$SUB"_B0_phase -div 100 -mul 3.141592653589793116
    #     -odt float $FUNCDIR/"$SUB"_B0_phase_rescaled

    # in_file --> out_file
    phase_radians = Node(fsl.ImageMaths(
        op_string='-mul 3.141592653589793116 -div 100',
        out_data_type='float',
        suffix='_radians',
    ), name='phaseRadians')

    workflow.connect(inputfiles, 'fmap_phasediff', phase_radians, 'in_file')

    # --- --- --- --- --- --- --- Unwrap Fieldmap --- --- --- --- --- ---
    # --- Unwrap phase
    # prelude -p $FUNCDIR/"$SUB"_B0_phase_rescaled
    #         -a $FUNCDIR/"$SUB"_B0_magnitude
    #         -o $FUNCDIR/"$SUB"_fmri_B0_phase_rescaled_unwrapped
    #         -m $FUNCDIR/"$SUB"_B0_magnitude_brain_mask
    #  magnitude_file, phase_file [, mask_file] --> unwrapped_phase_file
    unwrap = Node(PRELUDE(), name='unwrap')

    workflow.connect([
        (inputfiles, unwrap, [('fmap_magnitude', 'magnitude_file')]),
        (inputfiles, unwrap, [('fmap_mask', 'mask_file')]),
        (phase_radians, unwrap, [('out_file', 'phase_file')]),
    ])

    # --- --- --- --- --- --- --- Convert to Radians / Sec --- --- --- --- ---
    # fslmaths $FUNCDIR/"$SUB"_B0_phase_rescaled_unwrapped
    #          -mul 200 $FUNCDIR/"$SUB"_B0_phase_rescaled_unwrapped
    rescale = Node(fsl.ImageMaths(
        op_string='-mul 200',
    ), name='rescale')

    workflow.connect(unwrap, 'unwrapped_phase_file',
                     rescale, 'in_file')

    # --- --- --- --- --- --- --- Unmask fieldmap --- --- --- --- ---

    unmask_phase = Node(
        FUGUE(
            save_unmasked_fmap=True,
        ),
        name='unmask_phase')

    workflow.connect(rescale, 'out_file', unmask_phase, 'fmap_in_file')
    workflow.connect(inputfiles, 'fmap_mask', unmask_phase, 'mask_file')
    workflow.connect(infosource, 'unwarp_direction',
                     unmask_phase, 'unwarp_direction')

    # --- --- --- --- --- --- --- Undistort functionals --- --- --- --- ---
    # phasemap_in_file = phasediff
    # mask_file = mask
    # in_file = functional image
    # dwell_time = 0.0005585 s
    # unwarp_direction

    fugue_undistort = MapNode(
        FUGUE(
            dwell_time=0.0005585,
            # based on Process-NHP-MRI/Process_functional_data.md:
            asym_se_time=0.020,
            smooth3d=2.0,
            median_2dfilter=True,
        ),
        name='fugue',
        iterfield=['in_file'])

    workflow.connect(unmask_phase, 'fmap_out_file',
                     fugue_undistort, 'fmap_in_file')
    workflow.connect(inputfiles, 'fmap_mask',
                     fugue_undistort, 'mask_file')
    workflow.connect(inputfiles, 'func',
                     fugue_undistort, 'in_file')
    workflow.connect(infosource, 'unwarp_direction',
                     fugue_undistort, 'unwarp_direction')

    workflow.connect(fugue_undistort, 'unwarped_file',
                     outputfiles, 'undistorted')

    undistort_manmasks = fugue_undistort.clone('undistort_manmasks')
    workflow.connect(unmask_phase, 'fmap_out_file',
                     undistort_manmasks, 'fmap_in_file')
    workflow.connect(inputfiles, 'fmap_mask',
                     undistort_manmasks, 'mask_file')
    workflow.connect(inputfiles, 'func_manmask',
                     undistort_manmasks, 'in_file')
    workflow.connect(infosource, 'unwarp_direction',
                     undistort_manmasks, 'unwarp_direction')

    workflow.connect(undistort_manmasks, 'unwarped_file',
                     outputfiles, 'undistorted_manmasks')

    workflow.stop_on_first_crash = True
    workflow.keep_inputs = True
    workflow.remove_unnecessary_outputs = False
    workflow.write_graph()
    workflow.run()

if __name__ == '__main__':
    run_workflow()
