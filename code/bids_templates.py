
subject_list = ['eddy']
session_list = [
    '20170511',
    # '20170525',
    # '20170614',
    # '20170621',
]
    # '20170607',
datatype_list = ['func', 'anat', 'fmap']

templates = {
    'images': 'sub-{subject_id}/ses-{session_id}/{datatype}/'
              'sub-{subject_id}_ses-{session_id}_*.nii.gz',
}
