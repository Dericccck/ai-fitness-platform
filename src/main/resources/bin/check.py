#!/usr/bin/env python
# -*- coding: utf-8 -*- 
import sys
import json
import unittest
import requests


class TestUM(unittest.TestCase):
    def setUp(self):
        pass
    def test_health(self):
        url = 'http://localhost:8599/api/health/check'
        response = requests.get(url)
        self.assertTrue(response.ok)

    def test_home_feed_top(self):
        url = 'http://localhost:8599/api/feed/top?code=homeFeed'
        response = requests.get(url)
        self.assertTrue(response.ok)
        json = response.json()
        self.assertEqual(json['code'],200)
        self.assertGreater(len(json['data']),-1)
    def test_home_feed_bottom(self):
        url = 'http://localhost:8599/api/feed/bottom?code=homeFeed&limit=10'
        response = requests.get(url)
        self.assertTrue(response.ok)
        json = response.json()
        self.assertEqual(json['code'],200)
        self.assertGreater(len(json['data']),1)
    def test_event_feed_top(self):
        url = 'http://localhost:8599/api/feed/top?code=eventFeed'
        response = requests.get(url)
        self.assertTrue(response.ok)
        json = response.json()
        self.assertEqual(json['code'],200)
        self.assertGreater(len(json['data']),-1)
    def test_event_feed_bottom(self):
        url = 'http://localhost:8599/api/feed/bottom?code=eventFeed&limit=10'
        response = requests.get(url)
        self.assertTrue(response.ok)
        json = response.json()
        self.assertEqual(json['code'],200)
        self.assertGreater(len(json['data']),1)
    def test_all_works_list(self):
        url = 'http://localhost:8599/api/contest/item/works?limit=10'
        response = requests.get(url)
        self.assertTrue(response.ok)
        json = response.json()
        self.assertEqual(json['code'],200)
        self.assertGreater(len(json['data']),1)
    def test_all_works_search(self):
        url = 'http://localhost:8599/api/contest/item/works?limit=2&search=舞'
        response = requests.get(url)
        self.assertTrue(response.ok)
        json = response.json()
        self.assertEqual(json['code'],200)
        self.assertGreater(len(json['data']),-1)
    def test_login_as_parameter(self):
        url = 'http://localhost:8599/login?phone=32187654321&code=93821'
        response = requests.post(url)
        self.assertTrue(response.ok)
        json = response.json()
        self.assertEqual(json['code'],200)
        self.assertIsNotNone(len(json['data']))
    def test_login_as_from(self):
        url = 'http://localhost:8599/login'
        response = requests.post(url,data={'phone':'32187654321','code':'93821'})
        self.assertTrue(response.ok)
        json = response.json()
        self.assertEqual(json['code'],200)
        self.assertIsNotNone(len(json['data']))

if __name__ == '__main__':
    unittest.main()
