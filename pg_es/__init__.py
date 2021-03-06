""" Elastic Search foreign data wrapper """
# pylint: disable=super-on-old-class, import-error, unexpected-keyword-arg, broad-except, line-too-long

import httplib
import json
import logging

from elasticsearch import Elasticsearch

from multicorn import ForeignDataWrapper
from multicorn.utils import log_to_postgres as log2pg


class ElasticsearchFDW(ForeignDataWrapper):
    """ Elastic Search Foreign Data Wrapper """

    @property
    def rowid_column(self):
        """ Returns a column name which will act as a rowid column for
            delete/update operations.

            This can be either an existing column name, or a made-up one. This
            column name should be subsequently present in every returned
            resultset. """

        return self._rowid_column

    def __init__(self, options, columns):
        super(ElasticsearchFDW, self).__init__(options, columns)

        self.index = options.get('index', '')
        self.doc_type = options.get('type', '')
        self.query_column = options.get('query_column', None)
        self._rowid_column = options.get('rowid_column', 'id')

        self.client = Elasticsearch([{
            'host': options.get('host', 'localhost'),
            'port': int(options.get('port', '9200'))
        }])

        self.columns = columns

    def get_rel_size(self, quals, columns):
        """ Helps the planner by returning costs.
            Returns a tuple of the form (number of rows, average row width) """

        try:
            query = self._get_query(quals)

            if query:
                response = self.client.count(
                    index=self.index,
                    doc_type=self.doc_type,
                    q=query
                )
            else:
                response = self.client.count(
                    index=self.index,
                    doc_type=self.doc_type
                )
            return (response['count'], len(columns) * 100)
        except Exception as exception:
            log2pg(
                "COUNT for /{index}/{doc_type} failed: {exception}".format(
                    index=self.index,
                    doc_type=self.doc_type,
                    exception=exception
                ),
                logging.ERROR
            )
            return (0, 0)

    def execute(self, quals, columns):
        """ Execute the query """

        try:
            query = self._get_query(quals)

            if query:
                response = self.client.search(
                    index=self.index,
                    doc_type=self.doc_type,
                    q=query
                )
            else:
                response = self.client.search(
                    index=self.index,
                    doc_type=self.doc_type
                )
            return self._convert_response(response, columns, query)
        except Exception as exception:
            log2pg(
                "SEARCH for /{index}/{doc_type} failed: {exception}".format(
                    index=self.index,
                    doc_type=self.doc_type,
                    exception=exception
                ),
                logging.ERROR
            )
            return (0, 0)

    def insert(self, new_values):
        """ Insert new documents into Elastic Search """

        if self.rowid_column not in new_values:
            log2pg(
                'INSERT requires "{rowid}" column. Missing in: {values}'.format(
                    rowid=self.rowid_column,
                    values=new_values
                ),
                logging.ERROR
            )
            return (0, 0)

        document_id = new_values[self.rowid_column]
        new_values.pop(self.rowid_column, None)

        try:
            response = self.client.index(
                index=self.index,
                doc_type=self.doc_type,
                id=document_id,
                body=new_values
            )
            return response
        except Exception as exception:
            log2pg(
                "INDEX for /{index}/{doc_type}/{document_id} and document {document} failed: {exception}".format(
                    index=self.index,
                    doc_type=self.doc_type,
                    document_id=document_id,
                    document=new_values,
                    exception=exception
                ),
                logging.ERROR
            )
            return (0, 0)

    def update(self, document_id, new_values):
        """ Update existing documents in Elastic Search """

        new_values.pop(self.rowid_column, None)

        try:
            response = self.client.index(
                index=self.index,
                doc_type=self.doc_type,
                id=document_id,
                body=new_values
            )
            return response
        except Exception as exception:
            log2pg(
                "INDEX for /{index}/{doc_type}/{document_id} and document {document} failed: {exception}".format(
                    index=self.index,
                    doc_type=self.doc_type,
                    document_id=document_id,
                    document=new_values,
                    exception=exception
                ),
                logging.ERROR
            )
            return (0, 0)

    def delete(self, document_id):
        """ Delete documents from Elastic Search """

        try:
            response = self.client.delete(
                index=self.index,
                doc_type=self.doc_type,
                id=document_id
            )
            return response
        except Exception as exception:
            log2pg(
                "DELETE for /{index}/{doc_type}/{document_id} failed: {exception}".format(
                    index=self.index,
                    doc_type=self.doc_type,
                    document_id=document_id,
                    exception=exception
                ),
                logging.ERROR
            )
            return (0, 0)

    def _get_query(self, quals):
        if not self.query_column:
            return None

        return next(
            (
                qualifier.value
                for qualifier in quals
                if qualifier.field_name == self.query_column
            ),
            None
        )

    def _convert_response(self, data, columns, query):
        return [
            self._convert_response_row(row_data, columns, query)
            for row_data in data['hits']['hits']
        ]

    def _convert_response_row(self, row_data, columns, query):
        if query:
            # Postgres checks the query after too, so the query column needs to be present
            return dict(
                [
                    (column, self._convert_response_column(column, row_data))
                    for column in columns
                    if column in row_data['_source'] or column == self.rowid_column
                ]
                +
                [
                    (self.query_column, query)
                ]
            )
        return {
            column: self._convert_response_column(column, row_data)
            for column in columns
            if column in row_data['_source'] or column == self.rowid_column
        }

    def _convert_response_column(self, column, row_data):
        if column == self.rowid_column:
            return row_data['_id']
        return row_data['_source'][column]
