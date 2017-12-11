#!/usr/bin/env python

""" MultiQC module to parse output from Salmon """

from __future__ import print_function

import json
import logging
import os
from collections import OrderedDict

import numpy as np

from multiqc.modules.base_module import BaseMultiqcModule
from multiqc.modules.salmon.gcmodel import GCModel
from multiqc.plots import linegraph
from multiqc.plots import heatmap

# Initialise the logger
log = logging.getLogger(__name__)


class MultiqcModule(BaseMultiqcModule):
    def __init__(self):

        # Initialise the parent object
        super(MultiqcModule, self).__init__(name='Salmon', anchor='salmon',
        href='http://combine-lab.github.io/salmon/',
        info="is a tool for quantifying the expression of transcripts using RNA-seq data.")

        # Parse meta information. JSON win!
        self.salmon_meta = dict()
        for f in self.find_log_files('salmon/meta'):
            # Get the s_name from the parent directory
            s_name = os.path.basename(os.path.dirname(f['root']))
            s_name = self.clean_s_name(s_name, f['root'])
            self.salmon_meta[s_name] = json.loads(f['f'])
        # Parse Fragment Length Distribution logs
        self.salmon_fld = dict()
        self.salmon_gc = []
        for f in self.find_log_files('salmon/fld'):
            # Get the s_name from the parent directory
            if os.path.basename(f['root']) == 'libParams':
                s_name = os.path.basename(os.path.dirname(f['root']))
                s_name = self.clean_s_name(s_name, f['root'])
                self.parse_gc_bias(f['root'])
                parsed = OrderedDict()
                for i, v in enumerate(f['f'].split()):
                    parsed[i] = float(v)
                if len(parsed) > 0:
                    if s_name in self.salmon_fld:
                        log.debug("Duplicate sample name found! Overwriting: {}".format(s_name))
                    self.add_data_source(f, s_name)
                    self.salmon_fld[s_name] = parsed
        # Filter to strip out ignored sample names
        self.salmon_meta = self.ignore_samples(self.salmon_meta)
        self.salmon_fld = self.ignore_samples(self.salmon_fld)

        if len(self.salmon_meta) == 0 and len(self.salmon_fld) == 0:
            raise UserWarning
        if len(self.salmon_meta) > 0:
            log.info("Found {} meta reports".format(len(self.salmon_meta)))
            self.write_data_file(self.salmon_meta, 'multiqc_salmon')
        if len(self.salmon_fld) > 0:
            log.info("Found {} fragment length distributions".format(len(self.salmon_fld)))

        # Add alignment rate to the general stats table
        headers = OrderedDict()
        headers['percent_mapped'] = {
            'title': '% Aligned',
            'description': '% Mapped reads',
            'max': 100,
            'min': 0,
            'suffix': '%',
            'scale': 'YlGn'
        }
        headers['num_mapped'] = {
            'title': 'M Aligned',
            'description': 'Mapped reads (millions)',
            'min': 0,
            'scale': 'PuRd',
            'modify': lambda x: float(x) / 1000000,
            'shared_key': 'read_count'
        }
        self.general_stats_addcols(self.salmon_meta, headers)

        # Fragment length distribution plot
        pconfig = {
            'smooth_points': 500,
            'id': 'salmon_plot',
            'title': 'Salmon: Fragment Length Distribution',
            'ylab': 'Fraction',
            'xlab': 'Fragment Length (bp)',
            'ymin': 0,
            'xmin': 0,
            'tt_label': '<b>{point.x:,.0f} bp</b>: {point.y:,.0f}',
        }
        self.add_section(plot=linegraph.plot(self.salmon_fld, pconfig))
        self.plot_gc_bias()

    def plot_gc_bias(self):
        pconfig = lambda x: {
            'smooth_points': 25,
            'id': 'salmon_gc_plot {}'.format(x),
            'title': 'GC Bias {}'.format(x),
            'ylab': 'Obs/Exp ratio',
            'xlab': 'bins',
            'ymin': 0,
            'xmin': 0,
        }
        qrt1, qrt2, qrt3 = {}, {}, {}
        exp_avg = np.zeros(shape=(3, 25))
        obs_avg = np.zeros(shape=(3, 25))
        low, medium, high = [], [], []
        sample_names = []
        complete_avgs = []
        for sample_name, sample_gc in self.salmon_gc:
            sample_names.append(sample_name)
            exp = np.multiply(np.array(sample_gc.exp_weights_)[:, np.newaxis], sample_gc.exp_)
            obs = np.multiply(np.array(sample_gc.obs_weights_)[:, np.newaxis], sample_gc.obs_)
            exp_avg += exp
            obs_avg += obs
            ratio = np.divide(obs, exp)
            low.append(ratio[0])
            medium.append(ratio[1])
            high.append(ratio[2])
            complete_avgs.append(np.average([ratio[0], ratio[1], ratio[2]], axis=1))
            qrt1[sample_name] = self.scale(ratio[0], 100)
            qrt2[sample_name] = self.scale(ratio[1], 100)
            qrt3[sample_name] = self.scale(ratio[2], 100)
        low_bias_coeff = np.corrcoef(low)
        medium_bias_coeff = np.corrcoef(medium)
        high_bias_coeff = np.corrcoef(high)
        complete_avgs_coeff = np.corrcoef(complete_avgs)
        ratio_avg = np.divide(obs_avg, exp_avg)
        low_bias = self.scale(ratio_avg[0], 100)
        med_bias = self.scale(ratio_avg[1], 100)
        high_bias = self.scale(ratio_avg[2], 100)
        avg_plot = {'low-bias': low_bias, 'medium-bias': med_bias, 'high-bias': high_bias}
        self.add_section(plot=linegraph.plot(qrt1, pconfig('Low')))
        self.add_section(plot=linegraph.plot(qrt2, pconfig('Medium')))
        self.add_section(plot=linegraph.plot(qrt3, pconfig('High')))
        self.add_section(plot=linegraph.plot(avg_plot, pconfig('Average')))
        self.add_section(plot=linegraph.plot(avg_plot, pconfig('Average')))
        self.add_section(plot=heatmap.plot(low_bias_coeff, sample_names))
        self.add_section(plot=heatmap.plot(medium_bias_coeff, sample_names))
        self.add_section(plot=heatmap.plot(high_bias_coeff, sample_names))
        self.add_section(plot=heatmap.plot(complete_avgs_coeff, sample_names))

    def parse_gc_bias(self, f_root):
        bias_dir = os.path.dirname(f_root)
        sample_name = os.path.basename(os.path.dirname(bias_dir))
        is_exp_gc_exists = os.path.exists(os.path.join(bias_dir, 'aux_info', 'exp_gc.gz'))
        is_obs_gc_exists = os.path.exists(os.path.join(bias_dir, 'aux_info', 'obs_gc.gz'))
        if is_exp_gc_exists and is_obs_gc_exists:
            gc = GCModel()
            gc.from_file(bias_dir)
            self.salmon_gc.append((sample_name, gc))

    def scale(self, ratios, fragment_len):
        scaling_factor = fragment_len / (len(ratios))
        scaled_result = {}
        for i, ratio in enumerate(ratios):
            scaled_result[i * scaling_factor] = ratio
        return scaled_result
