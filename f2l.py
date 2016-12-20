#!/usr/bin/python

# option parser
import optparse

# system command functionality
from subprocess import call

# metadata read & write
import mutagen

# image thumbnails
from PIL import Image

# standard libs
import os
import unicodedata
import string
import sys
import base64

imsize = 240, 240

validFilenameChars = "-_.!()[]{}&~+^ %s%s%s" % (string.ascii_letters, string.digits, os.path.sep)


def removeDisallowedFilenameChars(filename):
    """ Removes disallowed characters from a string
    :param str filename: input filename to clean
    :return str: cleaned filename
    """
    try:
        filename = unicode(filename, encoding='utf-8')
    except UnicodeDecodeError:
        print "Error converting %s to unicode.  Skipping bad characters..." % filename
        filename = unicode(filename, encoding='utf-8', errors='ignore')
       
    cleanedFilename = unicodedata.normalize('NFKD', filename).encode('ASCII', 'ignore')
    return ''.join(c for c in cleanedFilename if c in validFilenameChars)


def getTracknumberStr(mf, format):
    """ Gets a track number string from tags
    :param mutagen.File mf: mutagen File object to inspect
    :param str format: file format to process
    :return str: string representation of the track number
    """
    if format in ('ogg', 'flac'):
        tmp = mf.get('tracknumber', None)  # Returns [trackstr]
        if tmp is None:
            return tmp
        tmp = tmp.split('/')[0]
        return "%02d" % int(tmp[0])
    elif format == 'm4a':
        tmp = mf.get('trkn', None)  # Returns [(track #, total tracks)] integers
        if tmp is None:
            return tmp
        return "%02d" % tmp[0][0]


def updateTags(ft, lossyt, format):
    """ Updates the tags in lossyt based on the tags in ft.
    :param mutagen.File ft: FLAC file tag structure to copy
    :param mutagen.File lossyt: lossy output file to tag
    :param str format: 'ogg' or 'm4a'
    """

    if format == 'ogg':
        lossyt.tags.clear()
        lossyt.tags.update(ft.tags)
      
    elif format == 'm4a':
        # We have to explicitly define how each tag is converted.  Sigh.
        lossyt['\xa9nam'] = ft.get('title', [u''])
        lossyt['\xa9ART'] = ft.get('artist', [u''])
        lossyt['\xa9alb'] = ft.get('album', [u''])
        lossyt['\xa9day'] = ft.get('date', [u''])
        lossyt['trkn'] = [(int(getTracknumberStr(ft, 'flac')), 0)]  # Don't bother with totaltracks
        lossyt['\xa9gen'] = ft.get('genre', [u''])
        # if ft.get('album artist') == [u'Various Artists']:
        #     lossyt['cpil'] = True
        lossyt['aART'] = ft.get('album artist', [u''])
        lossyt['\xa9wrt'] = ft.get('composer', [u''])
        if 'replaygain_album_gain' in ft.keys():
            lossyt['----:com.apple.iTunes:replaygain_album_gain'] = ft['replaygain_album_gain'][0].encode('ascii')
            lossyt['----:com.apple.iTunes:replaygain_track_gain'] = ft['replaygain_track_gain'][0].encode('ascii')


def does_dir_need_update(check_dir, dest_format, input_files):
    """ Compare input and transcoded dirs to see if there's any work to be done.
    :param str check_dir: directory for output files
    :param str dest_format: format of transcoded files to look for
    :param input_files: list of files in input directory, with paths
    :return bool: True if we should process this directory
    """
    # How many flac files do we have?
    num_flac = len([input_file for input_file in input_files if input_file.endswith('.flac')])
    if num_flac == 0:
        return False

    # If it doesn't exist, we need to transcode.
    if not os.path.isdir(check_dir):
        return True

    # Get the listing of the check dir.
    check_dir_files = [os.path.join(check_dir, check_file) for check_file in os.listdir(check_dir)]

    # How many of our target type?
    num_format = len([check_file for check_file in check_dir_files if check_file.endswith(dest_format)])

    if num_format < num_flac:
        return True

    # Is there a folder.jpg file in the source tree?
    if not any([f.endswith(os.sep + 'folder.jpg') for f in input_files]):
        print >> sys.stderr, "No album art for {} - Fix this ASAP!".format(os.path.basename(input_files[0]))
    elif not any([f.endswith(os.sep + 'folder.jpg') for f in check_dir_files]):
        # If there's a folder.jpg in the source, but not in the check_dir, process the folder.
        return True

    # What is the latest timestamp of the flac/folder.jpg files in the input_files?
    latest_source_time = max([os.path.getmtime(f) for f in input_files
                              if f.endswith('.flac') or f.endswith(os.sep + 'folder.jpg')])

    # What is the latest timestamp of the target type?
    latest_target_time = max([os.path.getmtime(f) for f in check_dir_files
                              if f.endswith(dest_format) or f.endswith(os.sep + 'folder.jpg')])

    if latest_source_time > latest_target_time:
        return True

    return False


def set_album_artist_tags(flacs):
    """ Sets the album artist tag properly for the collection.
    :param list[mutagen.File] flacs: list of flacs with information
    """
    unique_artists = set([f['artist'][-1] for f in flacs])
    for ft in flacs:
        # Clear out the 'albumartist' and 'va' tags, we use the one with spaces
        if 'albumartist' in ft:
            del ft['albumartist']
        if 'va' in ft:
            del ft['va']

        # Set the album artist tag to the main artist if it's not set
        if 'album artist' not in ft:
            if len(unique_artists) > 1:
                ft['album artist'] = [u'Various Artists']
            else:
                ft['album artist'] = [unique_artists.pop()]


def apply_rg_to_flacs(flacs, folder_of_flacs):
    """ Checks to see if replaygain has been applied to all tracks, and if not,
        run the replaygain call.
    :param list[mutagen.File] flacs: flac mutagen objects
    :param str folder_of_flacs: folder for replaygaining
    """
    # Check each track for the album gain tag
    # If any are missing, rerun metaflac --add-replay-gain *.flac on directory
    do_rg = not all('replaygain_album_gain' in f for f in flacs)

    if do_rg:
        rgcmd = 'metaflac --add-replay-gain *.flac'

        try:
            retcode = call(rgcmd, shell=True, cwd=folder_of_flacs)
            if retcode < 0:
                print >> sys.stderr, "Failure replaygaining flacs in folder %s", folder_of_flacs
                print >> sys.stderr, "  -  skipping folder and continuing to process..."
                return
        except Exception:
            print >> sys.stderr, "Failure replaygaining flacs in folder %s", folder_of_flacs
            print >> sys.stderr, "  -  skipping folder and continuing to process..."
            return


def flacdir2lossydir(thefolder, thefiles, flacroot, lossyroot, enc_ext, encopts, force_ascii,
                     check_rg, purge_orphaned, simulate, force_update):
    """ Processes a single folder with FLAC files, converting it to the format requested.
    :param str thefolder: current work folder
    :param list[str] thefiles: list of files in this directory, no paths
    :param str flacroot: root of the flac conversion, for relative path determination
    :param str lossyroot: output root of the lossy conversion
    :param str enc_ext: 'ogg' or 'm4a'
    :param str encopts: encoder options to be passed to the encoder exe
    :param bool force_ascii: convert characters in filenames to ascii
    :param bool check_rg: check the input flac files for albumgain, add it if missing
    :param bool purge_orphaned: mark folders that were touched with flac.exists files
    :param bool simulate: don't do anything, just simulate
    :param bool force_update: force a tag update on all lossy files
    """
    # Create the output directory name
    outdir = os.path.join(lossyroot, os.path.relpath(thefolder, flacroot))
    if force_ascii:
        outdir = removeDisallowedFilenameChars(outdir)

    input_files = [os.path.join(thefolder, thefile) for thefile in thefiles]
    flac_files = [input_file for input_file in input_files if input_file.endswith('.flac')]

    if len(flac_files) == 0:
        return

    if not force_update and not does_dir_need_update(outdir, enc_ext, input_files):
        if purge_orphaned:
            open(os.path.join(outdir, "flac.exists"), 'w').close()
        return

    # Load the flac tag structures into a list of mutagen.File objects
    flactags = [mutagen.File(input_file) for input_file in sorted(flac_files)]
    flacart = None if 'folder.jpg' not in thefiles else os.path.join(thefolder, 'folder.jpg')

    if check_rg:
        apply_rg_to_flacs(flactags, thefolder)
        # Re-open the flac files to get the new tags
        flactags = [mutagen.File(i.filename) for i in flactags]
       
    set_album_artist_tags(flactags)

    # Simulating means we can stop here
    if simulate:
        if purge_orphaned:
            open(os.path.join(outdir, "flac.exists"), "w").close()
        return

    # Check to see if the dir exists
    if not os.path.isdir(outdir):
        try:
            os.makedirs(outdir)
        except Exception:
            print >> sys.stderr, "Failure to create the folder %s", outdir
            print >> sys.stderr, "  -  skipping folder and continuing to process..."
            return

    # Get a list of the files in the lossy directory
    odirfiles = os.listdir(outdir)
    
    lossytags = [mutagen.File(os.path.join(outdir, i)) for i in odirfiles
                 if i.endswith("." + enc_ext)]
               
    # Make a mapping of track number to lossy file
    lossydict = {}
    for lt in lossytags:
        tn = getTracknumberStr(lt, enc_ext)
        if tn is None:
            # delete lossy files with bad tags or without tags
            os.unlink(lt.filename)
        else:
            lossydict[tn] = lt

    # Get the time of the source artwork
    flactime = 0
    if flacart:
        flactime = os.path.getmtime(flacart)

    # Get the time of the destination artwork
    lossytime = 0
    lossyart = os.path.join(outdir, 'folder.jpg')
    if 'folder.jpg' in odirfiles:
        lossytime = os.path.getmtime(lossyart)

    folderjpg = None
    # Do we need to update the artwork in the files?
    if flactime > 0 and (lossytime < flactime or force_update or
                         (len(lossytags) > 0 and 'covr' not in lossytags[0] and
                          'metadata_block_picture' not in lossytags[0])):

        # Resize the folder.jpg art and write to output dir
        im = Image.open(flacart)
        im.thumbnail(imsize, Image.ANTIALIAS)
        im.save(lossyart, "JPEG", quality=95)
        imwid, imhgt = im.size
        
        # Read the art file into a string to be put into the tags
        if os.path.exists(lossyart):
            with open(lossyart, 'rb') as albumArt:
                folderjpg = albumArt.read()
            pict = mutagen.flac.Picture()
            pict.data = folderjpg
            pict.type = 3
            pict.desc = u"Front Cover"
            pict.mime = u"image/jpeg"
            pict.width = imwid
            pict.height = imhgt
            pict.depth = 24

            # get the b64 encoded data
            picture_data = pict.write()
            folderjpg_encoded = base64.b64encode(picture_data).decode("ascii")

    printed = False
    # Loop over the flac files
    for ft in flactags:
    
        flac_tagnumber = getTracknumberStr(ft, 'flac')
       
        if not flac_tagnumber:
            print "Unable to parse tracknumber tag for files in %s, skipping dir..." % thefolder
            return
    
        lossyt = lossydict.get(flac_tagnumber, None)

        # If the ogg exists, update the tags
        if lossyt:
            # check time stamps on the flac and lossy files
            # if the flac file is newer, update the tags in the lossy file
            flactime = os.path.getmtime(ft.filename)
            lossytime = os.path.getmtime(lossyt.filename)
          
            saveit = False
            # Update tags if flac was updated for any reason
            if lossytime < flactime or force_update:
                updateTags(ft, lossyt, enc_ext)
                saveit = True

            # Update image if we loaded it earlier
            if folderjpg:
                if enc_ext== 'm4a':
                    lossyt['covr'] = [mutagen.mp4.MP4Cover(folderjpg, imageformat=mutagen.mp4.MP4Cover.FORMAT_JPEG)]
                elif enc_ext== 'ogg':
                    lossyt["metadata_block_picture"] = [folderjpg_encoded]
                saveit = True
          
            if saveit:
                if not printed:
                    print "Converting flacs in %s to %s..." % (thefolder, enc_ext)
                    printed = True

                print flac_tagnumber, "...",
                sys.stdout.flush()
                try:
                    lossyt.save()
                except Exception:
                    print >> sys.stderr, "Failure updating tags for file %s" % lossyt.filename
                    print >> sys.stderr, "  -  skipping file and continuing to process..."
                    continue
                # Update the timestamp on the lossy file
                os.utime(lossyt.filename, None)
       
        else:  # Create the transcoded file and tag it
       
            # create the lossy filename from the flac filename
            newname = os.path.basename(ft.filename)
            if force_ascii:
                newname = removeDisallowedFilenameChars(newname)
            newnamelong = os.path.join(outdir, os.path.splitext(newname)[0] + "." + enc_ext)
            if len(newnamelong) > 256:
                newname = os.path.join(outdir, os.path.splitext(newname)[0])
                newname = newname[:240] + "." + enc_ext
                print >> sys.stderr, "Filename too long, truncating to %s" % newname
            else:
                newname = newnamelong

            # transcode here
            if enc_ext== 'ogg':
                transcode_cmd = 'flac --totally-silent -d -c "%s" | wine /home/erik/bin/oggenc2.exe %s - -o "%s" > /dev/null 2>&1' % (ft.filename, encopts, newname)
            else:  # enc_ext== 'm4a':
                transcode_cmd = 'flac --totally-silent -d -c "%s" | neroAacEnc %s -ignorelength -if - -of "%s" > /dev/null 2>&1' % (ft.filename, encopts, newname)
          
            if not printed:
                print "Converting flacs in %s to %s..." % (thefolder, enc_ext)
                printed = True

            print flac_tagnumber,"...",
            sys.stdout.flush()
          
            try:
                retcode = call(transcode_cmd, shell=True)
                if retcode < 0:
                    print >> sys.stderr, "Failure converting %s to %s" % (ft.filename, newname)
                    print >> sys.stderr, "  -  skipping file and continuing to process..."
                    continue
            except Exception:
                print >> sys.stderr, "Failure converting %s to %s" % (ft.filename, newname)
                print >> sys.stderr, "  -  skipping file and continuing to process..."
                continue
             
            # set tags here
            nt = mutagen.File(newname)
            updateTags(ft, nt, enc_ext)
          
            # insert the artwork if we have any
            if folderjpg:
                if enc_ext == 'm4a':
                    nt['covr'] = [mutagen.mp4.MP4Cover(folderjpg, imageformat=mutagen.mp4.MP4Cover.FORMAT_JPEG)]
                elif enc_ext == 'ogg':
                    nt["metadata_block_picture"] = [folderjpg_encoded]
          
            nt.save()

    if printed:
        print
    
    if purge_orphaned:
        open(os.path.join(outdir, "flac.exists"), "w").close()


def dir_purge(thefolder, thefiles, *a, **k):

    # Load the transcoded tag structures into a list of mutagen.File objects
    audios = [i for i in sorted(thefiles) if (i.endswith(".ogg") or i.endswith(".m4a"))]
    
    if not audios:
        return

    if "flac.exists" in sorted(thefiles):
        os.remove(os.path.join(thefolder, "flac.exists"))
        return

    # Get rid of all of the files    
    print "Deleting %s ..." % thefolder

    if not k['simulate']:
        for i in thefiles:
            os.remove(os.path.join(thefolder, i))

        os.removedirs(thefolder)

                             
def get_options():
    p = optparse.OptionParser(usage="usage: %prog [options] flacrootdir lossyrootdir",
                              description='''This script creates a lossy mirror of a flac directory tree,
                                             recursively. Album art (named folder.jpg) will be resized to
                                             240x240 resolution and will be embedded in the tags & copied.''')

    p.add_option('-f', action='store', dest='enc_ext',
                 help='Lossy format to use. ogg and m4a are currently supported.')

    p.add_option('-o', action='store', dest='encopts', default='',
                 help='Option string to pass to the encoder.')

    p.add_option('-a', action='store_false', dest='force_ascii', default=True,
                 help='''Depending on the operating system, certain characters
                     may be illegal for use in filenames.  Additionally, accent
                     characters often cause problems for different applications.
                     When creating the output filename from the input filename,
                     the default behaviour of this script is to convert accented 
                     characters to unaccented ASCII, and strip any characters that
                     are illegal for use in filenames on some systems.  
                     Specifying -a will disable this translation, and will
                     copy the input filename to the output filename unaltered.''')

    p.add_option('-r', action='store_false', dest='check_rg', default=True,
                 help='''If you generally use replaygain tags in your flac files,
                     this script can help you make sure that all of your directories
                     have been properly replaygained. The default script behaviour
                     is to check each file for replaygain tags, and if they are
                     missing, add the tags prior to running the transcode.
                     Specifying -r will disable this check and transcode the flacs
                     without checking the replaygain values.''')

    p.add_option('-k', action='store_false', dest='purge_orphaned', default=True,
                 help='''By default, as the flac directory tree is traversed,
                     this script will put a small placeholder file in 'touched' 
                     directories.  When the tree traversals is complete, the script
                     will then walk the lossy tree ensuring that each directory with
                     audio files in it has this placeholder, otherwise it will delete
                     the directory as orphaned.  Specifying -k (keep) skips this
                     check and leaves all directories in the lossy tree intact.''')

    p.add_option('-s', action='store_true', dest='simulate', default=False,
                 help='''Simulate mode.  Generate the dirs, convert art, but do no
                     format conversion.  Don't delete anything, just indicate what will
                     happen.''')
                     
    p.add_option('-u', action='store_true', dest='force_update', default=False,
                 help='''Force updating the tags on all files.''')

    (opts, args) = p.parse_args()
    if len(args) < 2:
        p.error('At least two directory arguments are required,'
                'the source flac dir and the root of the transcoded tree.')
   
    if opts.enc_ext not in ('m4a', 'ogg'):
        p.error('Format must be m4a or ogg.  Aborting')
      
    return opts, args


def map_walk(f, path, *a, **k):
    """maps a given function to each folder found in the supplied path.

    :param function f: function to apply to each folder found
    :param path: root of path to walk
    :param *a: additional path args, the flacroot and lossyroot
    :param **k: key/value dict to pass as named params to the called function
    """
    # Passes in path args as *a (flacroot, lossyroot)
    map(lambda (folder, subfolders, files): f(folder, files, *a, **k), os.walk(path))


def main():

    # get command line options
    options, args = get_options()

    # call map_walk
    lossyroot = args[-1]
    for flacroot in args[0:-1]:
        modargs = [flacroot, lossyroot]
        map_walk(flacdir2lossydir, flacroot, *modargs, **options.__dict__)

    if options.purge_orphaned:
        map_walk(dir_purge, lossyroot, *args, **options.__dict__)
    
if __name__ == '__main__':
    main()

