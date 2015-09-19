#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2004-2015 Alterra, Wageningen-UR
# Allard de Wit (allard.dewit@wur.nl)
# and Zacharias Steinmetz (stei4785@uni-landau.de), Aug 2015
"""
A weather data provider reading its data from CSV files.
"""
import os
import datetime as dt
import csv

from ..base_classes import WeatherDataContainer, WeatherDataProvider
from ..util import reference_ET, angstrom, check_angstromAB
from ..exceptions import PCSEError
from ..settings import settings

class NoDataError(PCSEError):
    pass


class OutOfRange(PCSEError):
    pass

class CSVWeatherDataProvider(WeatherDataProvider):
    """Reading weather data from a CSV file.

    :param csv_fname: name of the CSV file to be read
    :param delimiter: CSV delimiter
    :param dateformat: date format to be read. Default is '%Y%m%d'
    :keyword ETmodel: "PM"|"P" for selecting Penman-Monteith or Penman
        method for reference evapotranspiration. Default is 'PM'.

    For reading weather data from a file, initially the CABOWeatherDataProvider
    was available which read its data from text in the CABO weather format.
    Nevertheless, building CABO weather files is tedious as for each year a new
    file must constructed. Moreover it is rather error prone and formatting
    mistakes are easily leading to errors.

    To simplify providing weather data to PCSE models, a new data provider
    has been derived from the ExcelWeatherDataProvider that reads its data
    from simple CSV files.

    The CSVWeatherDataProvider assumes that records are complete and does
    not make an effort to interpolate data as this can be easily
    accomplished in a text editor. Only SNOWDEPTH is allowed to be missing
    as this parameter is usually not provided outside the winter season.
    """
    translator = {
        'DAY': ['w_date', 'DAY'],
        'IRRAD': ['srad', 'RADIATION', 'IRRAD'],
        'TMIN': ['tmin', 'TEMPERATURE_MIN', 'TMIN'],
        'TMAX': ['tmax', 'TEMPERATURE_MAX', 'TMAX'],
        'VAP': ['vprs_tx', 'VAPOURPRESSURE', 'VAP'],
        'WIND': ['wind', 'WINDSPEED', 'WIND'],
        'RAIN': ['rain', 'PRECIPITATION', 'RAIN'],
        'SNOWDEPTH': ['snowdepth', 'SNOWDEPTH']
    }

    # Conversion functions
    NoConversion = lambda x: float(x)
    kJ_to_J = lambda x: float(x)*1000.
    mm_to_cm = lambda x: float(x)/10.
    csvdate_to_date = lambda x, dateformat: \
        dt.datetime.strptime(x, dateformat).date()

    obs_conversions = {
        "TMAX": NoConversion,
        "TMIN": NoConversion,
        "IRRAD": kJ_to_J,
        "DAY": csvdate_to_date,
        "VAP": NoConversion,
        "WIND": NoConversion,
        "RAIN": mm_to_cm,
        "SNOWDEPTH": NoConversion
    }

    def __init__(self, csv_fname, delimiter=',', dateformat='%Y%m%d',
                 ETmodel='PM'):
        WeatherDataProvider.__init__(self)

        self.dateformat = dateformat
        self.ETmodel = ETmodel
        self.fp_csv_fname = os.path.abspath(csv_fname)
        if not os.path.exists(self.fp_csv_fname):
            msg = "Cannot find weather file at: %s" % self.fp_csv_fname
            raise PCSEError(msg)

        if not self._load_cache_file(self.fp_csv_fname):    # Cache file cannot
                                                            # be loaded
            with open(csv_fname, newline='') as csv_file:
                self._read_meta(csv_file)
                self._read_observations(csv_file, delimiter, dateformat)
                self._write_cache_file(self.fp_csv_fname)

    def _read_meta(self, csv_file):
        timeout = dt.datetime.now() + dt.timedelta(seconds=30)
        line = str()
        while not line.startswith('## Daily weather data'):
            if dt.datetime.now() > timeout:
                raise RuntimeError
            else:
                exec(line) in dict()
                line = csv_file.readline()

        locs = locals()
        self.nodata_value = -99
        self.description = [u"Weather data for:",
                            u"Country: %s" % locs['Country'],
                            u"Station: %s" % locs['Station'],
                            u"Description: %s" % locs['Description'],
                            u"Source: %s" % locs['Source'],
                            u"Contact: %s" % locs['Contact']]

        self.longitude = float(locs['Longitude'])
        self.latitude = float(locs['Latitude'])
        self.elevation = float(locs['Elevation'])
        angstA = float(locs['AngstromA'])
        angstB = float(locs['AngstromB'])
        self.angstA, self.angstB = check_angstromAB(angstA, angstB)
        self.has_sunshine = bool(locs['HasSunshine'])

    def _read_observations(self, csv_file, delimiter, dateformat):
        obs = csv.reader(csv_file, delimiter=delimiter, quotechar='"')
        # Start reading all rows with data
        _headerrow = True
        for row in obs:
            try:
                # Save header row.
                if _headerrow:
                    header = row
                    for (i, item) in enumerate(header):
                        header[i] = ''.join(key for key, value in
                            self.translator.items() if item in value)
                    _headerrow = False
                else:
                    d = dict(zip(header, row))
                    # Delete rows not able to convert
                    if '' in d:
                        del d['']

                    for label in d.keys():
                        func = self.obs_conversions[label]
                        if label == 'DAY':
                            d[label] = func(d[label], dateformat)
                        else:
                            d[label] = func(d[label])

                        if d[label] == float('NaN') and label != "SNOWDEPTH":
                            raise NoDataError

                    if self.has_sunshine is True and 0 < d['IRRAD'] < 24:
                        d['IRRAD'] = angstrom(d["DAY"], self.latitude,
                            d['IRRAD'], self.angstA, self.angstB)

                    # Reference ET in mm/day
                    e0, es0, et0 = reference_ET(LAT=self.latitude,
                                                ELEV=self.elevation,
                                                ANGSTA=self.angstA,
                                                ANGSTB=self.angstB, **d)
                    # convert to cm/day
                    d["E0"] = e0/10.
                    d["ES0"] = es0/10.
                    d["ET0"] = et0/10.

                    wdc = WeatherDataContainer(LAT=self.latitude,
                                               LON=self.longitude,
                                               ELEV=self.elevation, **d)
                    self._store_WeatherDataContainer(wdc, d["DAY"])

            except ValueError as e:  # strange value in cell
                msg = "Failed reading row: %s. Skipping ..." % row
                self.logger.warn(msg)
                print(msg)

            except NoDataError as e: # Missing value encountered
                msg = "Missing value encountered at row %S. Skipping ..." % row
                self.logger.warn(msg)

    def _load_cache_file(self, csv_fname):

        cache_filename = self._find_cache_file(csv_fname)
        if cache_filename is None:
            return False
        else:
            self._load(cache_filename)
            return True

    def _find_cache_file(self, csv_fname):
        """Try to find a cache file for file name

        Returns None if the cache file does not exist, else it returns the full
        path to the cache file.
        """
        cache_filename = self._get_cache_filename(csv_fname)
        if os.path.exists(cache_filename):
            cache_date = os.stat(cache_filename).st_mtime
            csv_date = os.stat(csv_fname).st_mtime
            if cache_date > csv_date:  # cache is more recent then XLS file
                return cache_filename

        return None

    def _get_cache_filename(self, csv_fname):
        """Constructs the filename used for cache files given csv_fname
        """
        basename = os.path.basename(csv_fname)
        filename, ext = os.path.splitext(basename)

        tmp = "%s_%s.cache" % (self.__class__.__name__, filename)
        cache_filename = os.path.join(settings.METEO_CACHE_DIR, tmp)
        return cache_filename

    def _write_cache_file(self, csv_fname):

        cache_filename = self._get_cache_filename(csv_fname)
        try:
            self._dump(cache_filename)
        except (IOError, EnvironmentError) as e:
            msg = "Failed to write cache to file '%s' due to: %s" % (cache_filename, e)
            self.logger.warning(msg)
