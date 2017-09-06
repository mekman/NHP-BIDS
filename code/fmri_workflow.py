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

from nipype.workflows.fmri.fsl import (create_featreg_preproc,
                                       create_modelfit_workflow,
                                       create_fixed_effects_flow)

import preprocessing_workflow as preproc

"""
Preliminaries
-------------
Setup any package specific configuration. The output file format for FSL
routines is being set to compressed NIFTI.
"""

fsl.FSLCommand.set_default_output_type('NIFTI_GZ')

level1_workflow = pe.Workflow(name='level1flow')

preproc = preproc.create_workflow()  # create_featreg_preproc(whichvol='first')

modelfit = create_modelfit_workflow()

fixed_fx = create_fixed_effects_flow()

"""
Artifact detection is done in preprocessing workflow.
"""

"""
Add model specification nodes between the preprocessing and modelfitting
workflows.
"""
modelspec = pe.Node(model.SpecifyModel(), name="modelspec")

level1_workflow.connect([
    (preproc, modelspec,
     [('outputspec.motion_parameters', 'realignment_parameters'),
      ('outputspec.motion_outlier_files', 'outlier_files')]),
    (modelspec, modelfit,
     [('session_info', 'inputspec.session_info')]),
    (preproc, modelfit,
     [('outputspec.highpassed_files', 'inputspec.functional_data')]),
])


"""
Set up first-level workflow
---------------------------
"""


def sort_copes(files):
    numelements = len(files[0])
    outfiles = []
    for i in range(numelements):
        outfiles.insert(i, [])
        for j, elements in enumerate(files):
            outfiles[i].append(elements[i])
    return outfiles


def num_copes(files):
    return len(files)

pickfirst = lambda x: x[0]

level1_workflow.connect([(preproc, fixed_fx, [(('outputspec.mask', pickfirst),
                                               'flameo.mask_file')]),
                         (modelfit, fixed_fx, [(('outputspec.copes', sort_copes),
                                                'inputspec.copes'),
                                               ('outputspec.dof_file',
                                                'inputspec.dof_files'),
                                               (('outputspec.varcopes',
                                                 sort_copes),
                                                'inputspec.varcopes'),
                                               (('outputspec.copes', num_copes),
                                                'l2model.num_copes'),
                                               ])
                         ])

"""
Experiment specific components
------------------------------
The nipype tutorial contains data for two subjects.  Subject data
is in two subdirectories, ``s1`` and ``s2``.  Each subject directory
contains four functional volumes: f3.nii, f5.nii, f7.nii, f10.nii. And
one anatomical volume named struct.nii.
Below we set some variables to inform the ``datasource`` about the
layout of our data.  We specify the location of the data, the subject
sub-directories and a dictionary that maps each run to a mnemonic (or
field) for the run type (``struct`` or ``func``).  These fields become
the output fields of the ``datasource`` node in the pipeline.
In the example below, run 'f3' is of type 'func' and gets mapped to a
nifti filename through a template '%s.nii'. So 'f3' would become
'f3.nii'.
"""

# inputnode = pe.Node(niu.IdentityInterface(fields=['in_data']), name='inputnode')
inputnode = pe.Node(niu.IdentityInterface(fields=[
    'subject_id',
    'session_id',
]), name="inputnode")


# Specify the subject directories
subject_list = ['eddy']
session_list = ['20170511']

# Map field names to individual subject runs.
# info = dict(func=[['subject_id', ['f3', 'f5', 'f7', 'f10']]],
#             struct=[['subject_id', 'struct']])

infosource = pe.Node(niu.IdentityInterface(fields=['subject_id']),
                     name="infosource")

"""Here we set up iteration over all the subjects. The following line
is a particular example of the flexibility of the system.  The
``datasource`` attribute ``iterables`` tells the pipeline engine that
it should repeat the analysis on each of the items in the
``subject_list``. In the current example, the entire first level
preprocessing and estimation will be repeated for each subject
contained in subject_list.
"""

inputnode.iterables = [
    ('subject_id', subject_list),
    ('session_id', session_list),
]

"""
Now we create a :class:`nipype.interfaces.io.DataSource` object and
fill in the information from above about the layout of our data.  The
:class:`nipype.pipeline.NodeWrapper` module wraps the interface object
and provides additional housekeeping and pipeline specific
functionality.
"""

# The preprocessing workflow currently reads the image data.

level1_workflow.connect([
    (inputnode, preproc,
     [('subject_id', 'inputspec.subject_id'),
      ('session_id', 'inputspec.session_id'),
      ])
])


# datasource = pe.Node(nio.DataGrabber(infields=['subject_id'],
#                                                outfields=['func', 'struct']),
#                      name='datasource')
# datasource.inputs.template = 'nipype-tutorial/data/%s/%s.nii'
# datasource.inputs.template_args = info
# datasource.inputs.sort_filelist = True

"""
Use the get_node function to retrieve an internal node by name. Then set the
iterables on this node to perform two different extents of smoothing.
"""

featinput = level1_workflow.get_node('featpreproc.inputspec')
# featinput.iterables = ('fwhm', [5., 10.])
featinput.inputs.fwhm = 2.0

hpcutoff_s = 50.  # FWHM in seconds
TR = 2.5
hpcutoff_nvol = hpcutoff_s / 2.5  # FWHM in volumns
featinput.inputs.highpass = hpcutoff_nvol / 2.355  # Gaussian: σ in volumes

"""
Setup a function that returns subject-specific information about the
experimental paradigm. This is used by the
:class:`nipype.modelgen.SpecifyModel` to create the information necessary
to generate an SPM design matrix. In this tutorial, the same paradigm was used
for every participant. Other examples of this function are available in the
`doc/examples` folder. Note: Python knowledge required here.
"""

from timeevents.nipype import calc_curvetracing_events
timeevents = pe.MapNode(
    interface=calc_curvetracing_events,
    iterfield=('in_file', 'in_nvols'),
    name="timeevents")

def subjectinfo(subject_id):
    from nipype.interfaces.base import Bunch
    from copy import deepcopy

    print("Subject ID: %s\n" % str(subject_id))
    output = []
    names = ['Task-Odd', 'Task-Even']
    for r in range(4):
        onsets = [list(range(15, 240, 60)), list(range(45, 240, 60))]
        output.insert(r,
                      Bunch(conditions=names,
                            onsets=deepcopy(onsets),
                            durations=[[15] for s in names]))

    print("Need to implement subjectinfo function for my tasks.")
    import pdb
    pdb.set_trace()
    return output

"""
Setup the contrast structure that needs to be evaluated. This is a list of
lists. The inner list specifies the contrasts and has the following format -
[Name,Stat,[list of condition names],[weights on those conditions]. The
condition names must match the `names` listed in the `subjectinfo` function
described above.
"""

cont1 = ['Task>Baseline', 'T', ['Task-Odd', 'Task-Even'], [0.5, 0.5]]
cont2 = ['Task-Odd>Task-Even', 'T', ['Task-Odd', 'Task-Even'], [1, -1]]
cont3 = ['Task', 'F', [cont1, cont2]]
contrasts = [cont1, cont2]

modelspec.inputs.input_units = 'secs'
modelspec.inputs.time_repetition = TR
modelspec.inputs.high_pass_filter_cutoff = hpcutoff_s

modelfit.inputs.inputspec.interscan_interval = TR
modelfit.inputs.inputspec.bases = {'dgamma': {'derivs': False}}
modelfit.inputs.inputspec.contrasts = contrasts
modelfit.inputs.inputspec.model_serial_correlations = True
modelfit.inputs.inputspec.film_threshold = 1000

level1_workflow.base_dir = os.path.abspath('./fsl/workingdir')
level1_workflow.config['execution'] = dict(crashdump_dir=os.path.abspath('./fsl/crashdumps'))

level1_workflow.connect([
    (infosource, timeevents,
     [('eventlog', 'eventlog'),
      ('TR', 'TR'),
      ('nvols', 'nvols'),
      ]),
    # (inputnode, datasource, [('in_data', 'base_directory')]),
    # (infosource, datasource, [('subject_id', 'subject_id')]),
    # (infosource, modelspec, [(('subject_id', subjectinfo), 'subject_info')]),
    # (datasource, preproc, [('func', 'inputspec.func')]),
])

"""
Execute the pipeline
--------------------
The code discussed above sets up all the necessary data structures with
appropriate parameters and the connectivity between the processes, but does not
generate any output. To actually run the analysis on the data the
``nipype.pipeline.engine.Pipeline.Run`` function needs to be called.
"""

if __name__ == '__main__':
    # level1_workflow.write_graph()
    level1_workflow.run()
    # level1_workflow.run(plugin='MultiProc', plugin_args={'n_procs':2})