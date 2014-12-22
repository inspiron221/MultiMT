#!/usr/bin/env python
# -*- coding: utf-8 -*-
# ./tmtriangulate.py combine_given_weights -ps test/model1 -pt test/model2 -o test/phrase-table_sample -t tempdir
#  This class implement a naive method for triangulation: nothing
#  The most important part of this method is to initialize variables
#TODO: Implement a method for inversed : src-pvt ---> pvt-src phrase table
from __future__ import division, unicode_literals
import sys
import os
import gzip
import argparse
import copy
import re
from math import log, exp
from collections import defaultdict
from operator import mul
from tempfile import NamedTemporaryFile
#from tmcombine import Moses, Moses_Alignment, to_list
from subprocess import Popen


try:
    from itertools import izip
except:
    izip = zip

def parse_command_line():

    parser = argparse.ArgumentParser(description="Combine translation models. Check DOCSTRING of the class Triangulate_TMs() and its methods for a more in-depth documentation and additional configuration options not available through the command line. The function test() shows examples")


    group1 = parser.add_argument_group('Main options')
    group2 = parser.add_argument_group('More model combination options')
    group3 = parser.add_argument_group('Naive triangulation')

    group1.add_argument('action', metavar='ACTION', choices=["combine_given_weights","combine_given_tuning_set","combine_reordering_tables","compute_cross_entropy","return_best_cross_entropy","compare_cross_entropies"],
                    help='What you want to do with the models. One of %(choices)s.')

    group1.add_argument('-ps', metavar='DIRECTORY', dest='srcpvt',
                    help='model of the source and pivot, actually, it is going to be pivot-source')

    group1.add_argument('-pt', metavar='DIRECTORY', dest='pvttgt',
                    help='model of pivot and target')

    group1.add_argument('-w', '--weights', dest='weights', action=to_list,
                    default=None,
                    help='weight vector. Format 1: single vector, one weight per model. Example: \"0.1,0.9\" ; format 2: one vector per feature, one weight per model: \"0.1,0.9;0.5,0.5;0.4,0.6;0.2,0.8\"')

    group1.add_argument('-m', '--mode', type=str,
                    default="interpolate",
                    choices=["counts","interpolate","loglinear"],
                    help='basic mixture-model algorithm. Default: %(default)s. Note: depending on mode and additional configuration, additional statistics are needed. Check docstring documentation of Triangulate_TMs() for more info.')

    group1.add_argument('-r', '--reference', type=str,
                    default=None,
                    help='File containing reference phrase pairs for cross-entropy calculation. Default interface expects \'path/model/extract.gz\' that is produced by training a model on the reference (i.e. development) corpus.')

    group1.add_argument('-o', '--output', type=str,
                    default="-",
                    help='Output file (phrase table). If not specified, model is written to standard output.')

    group1.add_argument('--output-lexical', type=str,
                    default=None,
                    help=('Not only create a combined phrase table, but also combined lexical tables. Writes to OUTPUT_LEXICAL.e2f and OUTPUT_LEXICAL.f2e, or OUTPUT_LEXICAL.counts.e2f in mode \'counts\'.'))

    group1.add_argument('--lowmem', action="store_true",
                    help=('Low memory mode: requires two passes (and sorting in between) to combine a phrase table, but loads less data into memory. Only relevant for mode "counts" and some configurations of mode "interpolate".'))

    group1.add_argument('--tempdir', type=str,
                    default=None,
                    help=('Temporary directory in --lowmem mode.'))

    group1.add_argument('-t', '--tempdir2', dest='tempdir2', type=str,
                    default=None,
                    help=('Temporary directory to put the intermediate phrase'))


    group2.add_argument('--i_e2f', type=int,
                    default=0, metavar='N',
                    help=('Index of p(f|e) (relevant for mode counts if phrase table has custom feature order). (default: %(default)s)'))

    group2.add_argument('--i_e2f_lex', type=int,
                    default=1, metavar='N',
                    help=('Index of lex(f|e) (relevant for mode counts or with option recompute_lexweights if phrase table has custom feature order). (default: %(default)s)'))

    group2.add_argument('--i_f2e', type=int,
                    default=2, metavar='N',
                    help=('Index of p(e|f) (relevant for mode counts if phrase table has custom feature order). (default: %(default)s)'))

    group2.add_argument('--i_f2e_lex', type=int,
                    default=3, metavar='N',
                    help=('Index of lex(e|f) (relevant for mode counts or with option recompute_lexweights if phrase table has custom feature order). (default: %(default)s)'))

    group2.add_argument('--number_of_features', type=int,
                    default=4, metavar='N',
                    help=('Combine models with N + 1 features (last feature is constant phrase penalty). (default: %(default)s)'))

    group2.add_argument('--normalized', action="store_true",
                    help=('for each phrase pair x,y: ignore models with p(y)=0, and distribute probability mass among models with p(y)>0. (default: missing entries (x,y) are always interpreted as p(x|y)=0). Only relevant in mode "interpolate".'))

    group2.add_argument('--write-phrase-penalty', action="store_true",
      help=("Include phrase penalty in phrase table"))

    group2.add_argument('--recompute_lexweights', action="store_true",
                    help=('don\'t directly interpolate lexical weights, but interpolate word translation probabilities instead and recompute the lexical weights. Only relevant in mode "interpolate".'))

    group3.add_argument('--command', '--./tmtriangulate.py combine_given_weights -ps model1 -pt model2 -o output_phrasetable -t tempdir', action="store_true",
                    help=('If you wish to run the naive approach, the command above would work, in which: model1 = pivot-source model, model2 = pivot-target model'))


    return parser.parse_args()

#convert weight vector passed as a command line argument
class to_list(argparse.Action):
     def __call__(self, parser, namespace, weights, option_string=None):
         if ';' in weights:
             values = [[float(x) for x in vector.split(',')] for vector in weights.split(';')]
         else:
             values = [float(x) for x in weights.split(',')]
         setattr(namespace, self.dest, values)

#merge the noisy phrase table
class Merge_TM():
    """This class take input as one noisy phrase table in which it consists of so many repeated lines.
       The output of this class should be one final clean phrase table

       The tasks which have to be done are:
        + Merge TM by summing them up
        + Merge TM by taking the maximum
        + Prune TM by one way or another
    """
    def __init__(self,model=None,
                      output_file=None,
                      mode='interpolate',
                      lang_src=None,
                      lang_target=None,
                      output_lexical=None,
                      ):

        self.mode = mode
        self.model = model # the model file
        self.output_file = output_file
        self.lang_src = lang_src
        self.lang_target = lang_target
        self.loaded = defaultdict(int)
        self.output_lexical = output_lexical

    def _combine_TM(self,flag=False,prev_line=None):
        '''
        Summing up the probability
        Get the unification of alignment
        Get the sum of counts
        '''
        prev_line = []
        output_object = handle_file(self.output_file,'open',mode='w')
        count = 0
        sys.stderr.write("Start merging multiple lines ...")

        for line in self.model:
            # print counting lines
            if not count%100000:
                sys.stderr.write(str(count)+'...')
            count+=1

            line = self._load_line(line)
            if (flag):
                if (line[0] == prev_line[0] and line[1] == prev_line[1]):
                    # combine current sentence to previous sentence, return previous sentence
                    prev_line_tmp = self._combine_sum(prev_line, line)
                    continue
                else:
                # when you get out of the identical blog, print your previous sentence
                    outline = self._write_phrasetable_file(prev_line)
                    output_object.write(outline)
                    flag = False

            elif (prev_line):
                if (line[0] == prev_line[0] and line[1] == prev_line[1]):
                # if you see a second sentence in a block, turn flag to True and combine
                    flag = True
                else:
                    outline = self._write_phrasetable_file(prev_line)
                    output_object.write(outline)
            prev_line = line
        if (prev_line):
            outline = self._write_phrasetable_file(prev_line)
            output_object.write(outline)
        output_object.write("Done\n")
        handle_file(self.output_file,'close',output_object,mode='w')

    def _combine_sum(self,prev_line=None,cur_line=None):
        '''
        Summing up the probability
        Get the unification of alignment
        Get the sum of counts
        '''
        # probability
        for i in range(4):
            prev_line[2][i] += cur_line[2][i]
        # alignment
        for src,key in cur_line[3].iteritems():
            for tgt in key:
                if (tgt not in prev_line[3][src]):
                    prev_line[3][src].append(tgt)
        # count
        if (cur_line[4][0] != prev_line[4][0] or cur_line[4][1] != prev_line[4][1]):
            sys.exit(1)
        else:
            prev_line[4][2] += cur_line[4][2]
        return prev_line

    def _combine_maximum(self,prev_line=None,cur_line=None):
        '''
        Get the maximum the probability
        Get the unification of alignment
        Get the sum of counts
        '''
        # probability
        for i in range(4):
            prev_line[2][i] = max(prev_line[2], cur_line[2][i])
        # alignment
        for src,key in cur_line[3].iteritems():
            for tgt in key:
                if (tgt not in prev_line[3][src]):
                    prev_line[3][src].append(tgt)
        # count
        if (cur_line[4][0] != prev_line[4][0] or cur_line[4][1] != prev_line[4][1]):
            sys.exit(1)
        else:
            prev_line[4][2] += cur_line[4][2]
        return prev_line


    def _load_line(self,line):
        if (not line):
            return None
        ''' This function convert a string into an array of string and probability
        '''
        #print "line : ", line
        line = line.rstrip().split(b'|||')
        if line[-1].endswith(b' |||'):
            line[-1] = line[-1][:-4]
            line.append(b'')

        # remove the blank space
        line[0] = line[0].strip()
        line[1] = line[1].strip()

        # break the probability
        line[2]  = [float(i) for i in line[2].strip().split(b' ')]

        # break the alignment
        phrase_align = defaultdict(lambda: []*3)
        for pair in line[3].strip().split(b' '):
            try:
                s,t = pair.split(b'-')
                s,t = int(s),int(t)
                phrase_align[s].append(t)
            except:
                #print "Infeasible pair ", pair
                pass
        line[3] = phrase_align
        # break the count
        # sometimes, the count is too big
        line[4] = [long(float(i)) for i in line[4].strip().split(b' ')]

        return line
    def _write_phrasetable_file(self,line):
        '''
        write the phrase table line
        '''
        # convert data to appropriate format
        # probability
        src,tgt,features,alignment,word_counts = line[:5]
        features = b' '.join([b'%.6g' %(f) for f in features])

        alignments = []
        for src_id,tgt_id_list in alignment.iteritems():
            for tgt_id in sorted(tgt_id_list):
                alignments.append(str(src_id) + '-' + str(tgt_id))
        extra_space = b''
        if(len(alignments)):
            extra_space = b' '
        alignments = b' '.join(str(x) for x in alignments)

        word_counts = b' '.join([b'%.6g' %(f) for f in word_counts])

        line = b"%s ||| %s ||| %s ||| %s%s||| %s ||| |||\n" %(src,tgt,features,alignments,extra_space,word_counts)
        return line


class Triangulate_TMs():
    """This class handles the various options, checks them for sanity and has methods that define what models to load and what functions to call for the different tasks.
       Typically, you only need to interact with this class and its attributes.

    """

    #some flags that change the behaviour during scoring. See init docstring for more info
    flags = {'normalized':False,
            'recompute_lexweights':False,
            'intersected_cross-entropies':False,
            'normalize_s_given_t':None,
            'normalize-lexical_weights':True,
            'add_origin_features':False,
            'write_phrase_penalty':False,
            'lowmem': False,
            'i_e2f':0,
            'i_e2f_lex':1,
            'i_f2e':2,
            'i_f2e_lex':3
            }

    # each model needs a priority. See init docstring for more info
    _priorities = {'primary':1,
                    'map':2,
                    'supplementary':10}

    def __init__(self,model1=None,
                      model2=None,
                      weights=None,
                      output_file=None,
                      mode='interpolate',
                      number_of_features=4,
                      lang_src=None,
                      lang_target=None,
                      output_lexical=None,
                      **flags):

        self.mode = mode
        self.output_file = output_file
        self.lang_src = lang_src
        self.lang_target = lang_target
        self.loaded = defaultdict(int)
        self.output_lexical = output_lexical
        self.flags = copy.copy(self.flags)
        self.flags.update(flags)

        self.flags['i_e2f'] = int(self.flags['i_e2f'])
        self.flags['i_e2f_lex'] = int(self.flags['i_e2f_lex'])
        self.flags['i_f2e'] = int(self.flags['i_f2e'])
        self.flags['i_f2e_lex'] = int(self.flags['i_f2e_lex'])

        # Variable 'mode' is preserved to prepare for multiple way of trianuglating.
        # At this moment, it is  interpolate
        if mode not in ['interpolate']:
            sys.stderr.write('Error: mode must be either "interpolate", "loglinear" or "counts"\n')
            sys.exit(1)

        #models,number_of_features = self._sanity_checks(models,number_of_features)
        number_of_features = int(number_of_features)
        self.model1=model1
        self.model2=model2

        #self.score = score_interpolate

    def _sanity_checks(self,models,number_of_features):
        """check if input arguments make sense
           this function is important in TMCombine
           TODO: Think how to use this function in triangulation, which feature is necessary to check
        """
        #Note: This is a function which I borrow from TMCombine, however, it has not been used at all :)
        return None


    def _ensure_loaded(self,data):
        """load data (lexical tables; reference alignment; phrase table), if it isn't already in memory"""
        #Note:  This is a function which I borrow from TMCombine, however, it has not been used at all :)

        if 'lexical' in data:
            self.model_interface.require_alignment = True

        if 'reference' in data and not self.loaded['reference']:

            sys.stderr.write('Loading word pairs from reference set...')
            self.reference_interface.load_word_pairs(self.lang_src,self.lang_target)
            sys.stderr.write('done\n')
            self.loaded['reference'] = 1

        if 'lexical' in data and not self.loaded['lexical']:

            sys.stderr.write('Loading lexical tables...')
            self.model_interface.load_lexical_tables(self.models,self.mode)
            sys.stderr.write('done\n')
            self.loaded['lexical'] = 1

        if 'pt-filtered' in data and not self.loaded['pt-filtered']:

            models_prioritized = [(self.model_interface.open_table(model,'phrase-table'),priority,i) for (model,priority,i) in priority_sort_models(self.models)]

            for model,priority,i in models_prioritized:
                sys.stderr.write('Loading phrase table ' + str(i) + ' (only data relevant for reference set)')
                j = 0
                for line in model:
                    if not j % 1000000:
                        sys.stderr.write('...'+str(j))
                    j += 1
                    line = line.rstrip().split(b' ||| ')
                    if line[-1].endswith(b' |||'):
                        line[-1] = line[-1][:-4]
                        line.append('')
                    self.model_interface.load_phrase_features(line,priority,i,store='all',mode=self.mode,filter_by=self.reference_interface.word_pairs,filter_by_src=self.reference_interface.word_source,filter_by_target=self.reference_interface.word_target,flags=self.flags)
                sys.stderr.write(' done\n')

            self.loaded['pt-filtered'] = 1

        if 'lexical-filtered' in data and not self.loaded['lexical-filtered']:
            e2f_filter, f2e_filter = _get_lexical_filter(self.reference_interface,self.model_interface)

            sys.stderr.write('Loading lexical tables (only data relevant for reference set)...')
            self.model_interface.load_lexical_tables(self.models,self.mode,e2f_filter=e2f_filter,f2e_filter=f2e_filter)
            sys.stderr.write('done\n')
            self.loaded['lexical-filtered'] = 1

        if 'pt-target' in data and not self.loaded['pt-target']:

            models_prioritized = [(self.model_interface.open_table(model,'phrase-table'),priority,i) for (model,priority,i) in priority_sort_models(self.models)]

            for model,priority,i in models_prioritized:
                sys.stderr.write('Loading target information from phrase table ' + str(i))
                j = 0
                for line in model:
                    if not j % 1000000:
                        sys.stderr.write('...'+str(j))
                    j += 1
                    line = line.rstrip().split(b' ||| ')
                    if line[-1].endswith(b' |||'):
                        line[-1] = line[-1][:-4]
                        line.append('')
                    self.model_interface.load_phrase_features(line,priority,i,mode=self.mode,store='target',flags=self.flags)
                sys.stderr.write(' done\n')

            self.loaded['pt-target'] = 1

    ############################################################################################################
    def combine_standard(self,weights=None):
        """write a new phrase table, based on existing weights of two other tables"""
        data = []

        if self.mode == 'interpolate':
            if self.flags['recompute_lexweights']:
                data.append('lexical')
            if self.flags['normalized'] and self.flags['normalize_s_given_t'] == 't' and not self.flags['lowmem']:
                data.append('pt-target')

        #self._ensure_loaded(data)

        if self.flags['lowmem'] and (self.mode == 'counts' or self.flags['normalized'] and self.flags['normalize_s_given_t'] == 't'):
            self._inverse_wrapper(weights,tempdir=self.flags['tempdir'])
        else:
            file1obj = handle_file(os.path.join(self.model1,'model','phrase-table'), 'open', 'r')
            file2obj = handle_file(os.path.join(self.model2,'model','phrase-table'), 'open', 'r')
            model1 = (file1obj, 1, 1)
            model2 = (file2obj, 1, 2)

            #print model1, model2, self.mode
            output_object = handle_file(self.output_file,'open',mode='w')
            self._write_phrasetable(model1, model2, output_object)
            handle_file(self.output_file,'close',output_object,mode='w')

    def _load_line(self,line):
        # nothing found, nothing return
        if (not line):
            return None
        ''' This function convert a string into an array of string and probability
        '''
        line = line.rstrip().split(b'|||')
        if line[-1].endswith(b' |||'):
            line[-1] = line[-1][:-4]
            line.append(b'')

        return line


    def _phrasetable_traversal(self,model1,model2,prev_line1,prev_line2,deci,output_object,iteration):
        ''' A non-recursive way to read two model
            Notes: In moses phrase table, the longer phrase appears earlier than the short phrase
        '''
        line1 =  self._load_line(model1[0].readline())
        line2 =  self._load_line(model2[0].readline())
        count = 0
        while(1):
            if not count%100000:
                sys.stderr.write(str(count)+'...')
            count+=1
            #if (count > 300000 and count < 400000):
                #print line1, line2
            if (self.phrase_equal[0]):
                if (line1 and line1[0] == self.phrase_equal[0]):
                    self.phrase_equal[1].append(line1)
                    line1 =  self._load_line(model1[0].readline())
                    continue
                elif (line2 and line2[0] == self.phrase_equal[0]):
                    self.phrase_equal[2].append(line2)
                    line2 = self._load_line(model2[0].readline())
                    continue
                else:
                    self._combine_and_print(output_object)

            # handle if the matching is found
            if (not line1 or not line2):
                #self.phrase_equal = defaultdict(lambda: []*3)
                self._combine_and_print(output_object)
                sys.stderr.write("Finish loading\n")
                return None

            # handle if the machine is not found
            if (not self.phrase_equal[0]):
                if (line1[0] == line2[0]):
                    self.phrase_equal[0] = line1[0]
                elif (line1[0].startswith(line2[0])):
                    line1 = self._load_line(model1[0].readline())
                elif (line2[0].startswith(line1[0])):
                    line2 = self._load_line(model2[0].readline())
                elif (line1[0] < line2[0]):
                    line1 = self._load_line(model1[0].readline())
                elif (line1[0] > line2[0]):
                    line2 = self._load_line(model2[0].readline())

    def _combine_and_print(self,output_object):
        ''' Follow Cohn at el.2007
        The conditional over the source-target pair is: p(s|t) = sum_i p(s|i,t)p(i|t) = sum_i p(s|i)p(i|t)
        in which i is the pivot which could be found in model1(pivot-src) and model2(src-tgt)
        After combining two phrase-table, print them right after it
        '''
        #if (len(self.phrase_equal[1]) > 10 and len(self.phrase_equal[2]) > 10):
            #print "Huge match: ", self.phrase_equal[1][0][0], len(self.phrase_equal[1]), len(self.phrase_equal[2])
        for phrase1 in self.phrase_equal[1]:
            for phrase2 in self.phrase_equal[2]:
                if (phrase1[0] != phrase2[0]):
                    sys.exit("THE PIVOTS ARE DIFFERENT")
                src = phrase1[1].strip()
                tgt = phrase2[1].strip()
                if (not isinstance(phrase1[2],list)):
                    phrase1[2] = [float(i) for i in phrase1[2].strip().split()]
                if (not isinstance(phrase2[2],list)):
                    phrase2[2] = [float(j) for j in phrase2[2].strip().split()]

                #self.phrase_probabilities=[0]*4
                # A-B = A|B|P(A|B) L(A|B) P(B|A) L(B|A)
                # A-C = A|C|P(A|C) L(A|C) P(C|A) L(C|A)
                ## B-C = B|C|P(B|C) L(B|C) P(C|B) L(C|B)

                features = self._get_features(src, tgt, phrase1[2], phrase2[2])
                word_alignments = self._get_word_alignments(src, tgt, phrase1[3].strip(), phrase2[3].strip())
                word_counts = self._get_word_counts(src, tgt, phrase1[4].strip(), phrase2[4].strip())
                outline =  self._write_phrasetable_file(src,tgt,features,word_alignments,word_counts)
                output_object.write(outline)

        # reset the memory
        self.phrase_equal = None
        self.phrase_equal = defaultdict(lambda: []*3)

        self.phrase_equal = defaultdict(lambda: []*3)

    def _get_features(self,src,target,feature1,feature2):
        """from the Moses phrase table probability, get the new probability
           TODO: the phrase penalty value?
        """
        phrase_features =  [0]*4
        phrase_features[0] = feature1[2] * feature2[0]
        phrase_features[1] = feature1[3] * feature2[1]
        phrase_features[2] = feature1[0] * feature2[2]
        phrase_features[3] = feature1[1] * feature2[3]

        return phrase_features


    def _get_word_alignments(self,src,target,align1,align2):
        """from the Moses phrase table alignment info in the form "0-0 1-0",
           get the aligned word pairs / NULL alignments
        """
        phrase_align = defaultdict(lambda: []*3)
        # fill value to the phrase_align
        try:
            for pair in align1.split(b' '):
                p,s = pair.split(b'-')
                p,s = int(p),int(s)
                phrase_align[0].append(s)
            for pair in align2.split(b' '):
                p,t = pair.split(b'-')
                p,t = int(p),int(s)
                phrase_align[1].append(t)
        except:
            pass
        phrase_align[2] = defaultdict(lambda: []*3)
        for src_id in phrase_align[0]:
            for tgt_id in phrase_align[1]:
                if (tgt_id not in phrase_align[2][src_id]):
                    phrase_align[2][src_id].append(tgt_id)

        return phrase_align[2]


    def _get_word_counts(self,src,target,count1,count2):
        """from the Moses phrase table word count info in the form "1000 10 10",
           get the counts for src, tgt
           the word count is: target - src - both
        """
        word_count = [0]*3
        count1 = count1.split(b' ')
        count2 = count2.split(b' ')
        word_count[0] = long(float(count2[0]))
        word_count[1] = long(float(count1[0]))

        if (len(count1) > 2):
            word_count[2] = min(long(float(count1[2])),long(float(count2[2])))
        return word_count


    def _write_phrasetable(self,model1,model2,output_object,inverted=False):
        """Incrementally load phrase tables, calculate score for increment and write it to output_object"""
        # define which information we need to store from the phrase table
        # possible flags: 'all', 'target', 'source' and 'pairs'
        # interpolated models without re-normalization only need 'pairs', otherwise 'all' is the correct choice
        store_flag = 'all'
        if self.mode == 'interpolate' and not self.flags['normalized']:
            store_flag = 'pairs'

        sys.stderr.write('Incrementally loading and processing phrase tables...')
        # Start process phrase table
        self.phrase_equal = defaultdict(lambda: []*3)
        self._phrasetable_traversal(model1=model1, model2=model2, prev_line1=None, prev_line2=None, deci=0, output_object=output_object,iteration=0)

        sys.stderr.write("done")


    def _write_phrasetable_file(self,src,tgt,features,alignment,word_counts):
        ''' Output the line in Moses format
        '''
        # convert data to appropriate format
        features = b' '.join([b'%.6g' %(f) for f in features])

        alignments = []
        for src_id,tgt_id_list in alignment.iteritems():
            for tgt_id in sorted(tgt_id_list):
                alignments.append(str(src_id) + '-' + str(tgt_id))
        extra_space = b''
        if(len(alignments)):
            extra_space = b' '
        alignments = b' '.join(str(x) for x in alignments)

        word_counts = b' '.join([b'%.6g' %(f) for f in word_counts])

        line = b"%s ||| %s ||| %s ||| %s%s||| %s ||| |||\n" %(src,tgt,features,alignments,extra_space,word_counts)
        return line

# GLOBAL DEF
def handle_file(filename,action,fileobj=None,mode='r'):
    """support reading/writing either from/to file, stdout or gzipped file"""

    if action == 'open':

        if mode == 'r':
            mode = 'rb'
        elif mode == 'w':
            mode = 'wb'

        if mode == 'rb' and not filename == '-' and not os.path.exists(filename):
            if os.path.exists(filename+'.gz'):
                filename = filename+'.gz'
            else:
                sys.stderr.write('Error: unable to open file. ' + filename + ' - aborting.\n')

                if 'counts' in filename and os.path.exists(os.path.dirname(filename)):
                    sys.stderr.write('For a weighted counts combination, we need statistics that Moses doesn\'t write to disk by default.\n')
                    sys.stderr.write('Repeat step 4 of Moses training for all models with the option -write-lexical-counts.\n')

                exit(1)

        if filename.endswith('.gz'):
            fileobj = gzip.open(filename,mode)

        elif filename == '-' and mode == 'wb':
            fileobj = sys.stdout

        else:
                fileobj = open(filename,mode)

        return fileobj

    elif action == 'close' and filename != '-':
        fileobj.close()

def sort_file(filename,tempdir=None):
    """Sort a file and return temporary file"""

    cmd = ['sort', filename]
    env = {}
    env['LC_ALL'] = 'C'
    if tempdir:
        cmd.extend(['-T',tempdir])

    outfile = NamedTemporaryFile(delete=False,dir=tempdir)
    sys.stderr.write('LC_ALL=C ' + ' '.join(cmd) + ' > ' + outfile.name + '\n')
    p = Popen(cmd,env=env,stdout=outfile.file)
    p.wait()

    outfile.seek(0)

    return outfile


def dot_product(a,b):
    """calculate dot product from two lists"""

    # optimized for PyPy (much faster than enumerate/map)
    s = 0
    i = 0
    for x in a:
        s += x * b[i]
        i += 1

    return s



if __name__ == "__main__":

    if len(sys.argv) < 2:
        sys.stderr.write("no command specified. use option -h for usage instructions\n")

    elif sys.argv[1] == "test":
        test()

    else:
        args = parse_command_line()
        #initialize
        combiner = Triangulate_TMs(weights=args.weights,
                               model1=args.srcpvt,
                               model2=args.pvttgt,
                               mode=args.mode,
                               output_file=os.path.normpath('/'.join([args.tempdir2, 'phrase-table'])),
                               reference_file=args.reference,
                               output_lexical=args.output_lexical,
                               lowmem=args.lowmem,
                               normalized=args.normalized,
                               recompute_lexweights=args.recompute_lexweights,
                               tempdir=args.tempdir,
                               number_of_features=args.number_of_features,
                               i_e2f=args.i_e2f,
                               i_e2f_lex=args.i_e2f_lex,
                               i_f2e=args.i_f2e,
                               i_f2e_lex=args.i_f2e_lex,
                               write_phrase_penalty=args.write_phrase_penalty)

        # write everything to a file
        combiner.combine_standard()
        # sort the file
        tmpfile = sort_file(combiner.output_file,tempdir=args.tempdir2)
        #os.remove(combiner.output_file)
        # combine the new file
        merger = Merge_TM(model=tmpfile,
                          output_file=args.output,
                          mode=combiner.mode)
        merger._combine_TM()