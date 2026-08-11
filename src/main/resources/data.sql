SET FOREIGN_KEY_CHECKS=0;
--alter table article add fulltext key idx_article_title (title_with_space);
--alter table organization add fulltext key idx_organization_search (search);
--alter table works add fulltext key idx_works_name (name_with_space);
--alter table works add fulltext key idx_app_search (app_search);
--alter table contest_season add fulltext key idx_contest_season_search (search);
--alter table frog_rank add fulltext key idx_frog_rank_search (search);
--alter table contestant_info add fulltext key idx_contestant_info_search (search);
--alter table marketing add fulltext key idx_marketing_search (search);
--alter table works_card add fulltext key idx_works_card_search (search);
--alter table item add fulltext key idx_item_search (search);
--alter table certificate add fulltext key idx_certificate_search (search);

--15210160566顾总
--18600050995李总
--13811098131李总
--18500331143程磊


CREATE TABLE IF NOT EXISTS `shedlock` (
  `name` varchar(64) NOT NULL,
  `lock_until` timestamp(3) NULL DEFAULT NULL,
  `locked_at` timestamp(3) NULL DEFAULT NULL,
  `locked_by` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

insert ignore into login_user (id,beta,name, phone, avatar, edit_times,max_edit_times, accept_notify,available_votes,create_time) values ('00000000000000000000000000000001',false,'测试用户1', '12312345678','https://picsum.photos/50/50?id=1',0,1,true,1,date_add(current_date(),interval -1 day));
insert ignore into login_user (id,beta,name, phone, avatar, edit_times,max_edit_times, accept_notify,available_votes,create_time) values ('00000000000000000000000000000002',false,'程磊', '28500331143','https://picsum.photos/50/50?id=2',0,1,true,1,date_add(current_date(),interval -1 day));
insert ignore into login_user (id,beta,name, phone, avatar, edit_times,max_edit_times, accept_notify,available_votes,create_time) values ('00000000000000000000000000000003',false,'测试用户3', '15330013729','https://picsum.photos/50/50?id=3',0,1,true,1,date_add(current_date(),interval -1 day));
insert ignore into login_user (id,beta,name, phone, avatar, edit_times,max_edit_times, accept_notify,available_votes,create_time) values ('00000000000000000000000000000004',false,'测试用户4', '28500331104','https://picsum.photos/50/50?id=4',0,1,true,1,date_add(current_date(),interval -1 day));
insert ignore into login_user (id,beta,name, phone, avatar, edit_times,max_edit_times, accept_notify,available_votes,create_time) values ('00000000000000000000000000000005',false,'测试用户5', '15330013729','https://picsum.photos/50/50?id=5',0,1,true,1,date_add(current_date(),interval -1 day));
insert ignore into login_user (id,beta,name, phone, avatar, edit_times,max_edit_times, accept_notify,available_votes,create_time) values ('00000000000000000000000000000006',false,'未来之星组委会', '28500331106','https://picsum.photos/50/50?id=5',0,1,true,1,date_add(current_date(),interval -1 day));

insert ignore into follower (id,login_user_id,following_login_user_id,create_time) values ('00000000000000000000000000000001','00000000000000000000000000000001','00000000000000000000000000000002',date_add(current_date(),interval -1 day));
insert ignore into follower (id,login_user_id,following_login_user_id,create_time) values ('00000000000000000000000000000002','00000000000000000000000000000001','00000000000000000000000000000003',date_add(current_date(),interval -1 day));
insert ignore into follower (id,login_user_id,following_login_user_id,create_time) values ('00000000000000000000000000000003','00000000000000000000000000000001','00000000000000000000000000000004',date_add(current_date(),interval -1 day));
insert ignore into follower (id,login_user_id,following_login_user_id,create_time) values ('00000000000000000000000000000004','00000000000000000000000000000002','00000000000000000000000000000003',date_add(current_date(),interval -1 day));


insert ignore into login_user_authority (id,authority,login_user_id) values('00000000000000000000000000000001', 'ADMIN','00000000000000000000000000000001');
insert ignore into login_user_authority (id,authority,login_user_id,entity_id) values('00000000000000000000000000000002', 'ADMIN_ORGANIZATION','00000000000000000000000000000001', '00000000000000000000000000000001');
insert ignore into login_user_authority (id,authority,login_user_id) values('00000000000000000000000000000003', 'ADMIN','00000000000000000000000000000003');

insert ignore into organization (id, name,logo, create_login_user_id,virtual_organization) values ('00000000000000000000000000000001', '新东方在线','https://picsum.photos/512/512?orgizationId=00000000000000000000000000000001','00000000000000000000000000000001',true);
insert ignore into organization (id, name,logo, create_login_user_id,virtual_organization) values ('00000000000000000000000000000002', '旧东方在线','https://picsum.photos/512/512?orgizationId=00000000000000000000000000000002','00000000000000000000000000000001',true);

--insert ignore into contest (id, name, create_login_user_id) values('00000000000000000000000000000001', '未来之星','00000000000000000000000000000001');

insert ignore into contest_season(id, name,create_login_user_id,deleted) values ('00000000000000000000000000000001', '未来之星', '00000000000000000000000000000001',false);

insert ignore into contest_item(id,name,create_login_user_id) values ('00000000000000000000000000000001', '声乐', '00000000000000000000000000000001');
insert ignore into contest_item(id,name,create_login_user_id) values ('00000000000000000000000000000002', '舞蹈', '00000000000000000000000000000001');
insert ignore into contest_item(id,name,create_login_user_id) values ('00000000000000000000000000000003', '语言', '00000000000000000000000000000001');
insert ignore into contest_item(id,name,create_login_user_id) values ('00000000000000000000000000000004', '书画', '00000000000000000000000000000001');
insert ignore into contest_item(id,name,create_login_user_id) values ('00000000000000000000000000000005', '器乐', '00000000000000000000000000000001');
insert ignore into contest_item(id,name,create_login_user_id) values ('00000000000000000000000000000006', '表演', '00000000000000000000000000000001');
insert ignore into contest_item(id,name,create_login_user_id,parent_id) values ('00000000000000000000000000002001', '街舞','00000000000000000000000000000001','00000000000000000000000000000002');
insert ignore into contest_item(id,name,create_login_user_id,parent_id) values ('00000000000000000000000000002002', '拉丁舞','00000000000000000000000000000001','00000000000000000000000000000002');
insert ignore into contest_item(id,name,create_login_user_id,parent_id) values ('00000000000000000000000000002003', '其他舞蹈', '00000000000000000000000000000001','00000000000000000000000000000002');

insert ignore into contest_age_range(id,name,contest_season_id,start_time,end_time) values ('00000000000000000000000000000001', '幼儿组','00000000000000000000000000000001', '2013-01-01','2020-01-01');
insert ignore into contest_age_range(id,name,contest_season_id,start_time,end_time) values ('00000000000000000000000000000002', '儿童组','00000000000000000000000000000001', '2009-01-01','2013-01-01');
insert ignore into contest_age_range(id,name,contest_season_id,start_time,end_time) values ('00000000000000000000000000000003', '少年组','00000000000000000000000000000001', '2004-01-01','2009-01-01');

--insert ignore into contest_stage(id,name,contest_item_id,create_login_user_id) values('00000000000000000000000000000001', '海选','00000000000000000000000000000001', '00000000000000000000000000000001');
--insert ignore into contest_stage(id,name,contest_item_id,create_login_user_id) values('00000000000000000000000000000002', '海选','00000000000000000000000000000002', '00000000000000000000000000000001');
--insert ignore into contest_stage(id,name,contest_item_id,create_login_user_id) values('00000000000000000000000000000003', '海选','00000000000000000000000000000003', '00000000000000000000000000000001');
--insert ignore into contest_stage(id,name,contest_item_id,create_login_user_id) values('00000000000000000000000000000004', '海选','00000000000000000000000000000004', '00000000000000000000000000000001');

--insert ignore into contest_division(id,name,contest_stage_id,create_login_user_id) values('00000000000000000000000000000001', '中国区','00000000000000000000000000000001', '00000000000000000000000000000001');
--insert ignore into contest_division(id,name,contest_stage_id,create_login_user_id) values('00000000000000000000000000000002', '中国区','00000000000000000000000000000002', '00000000000000000000000000000001');
--insert ignore into contest_division(id,name,contest_stage_id,create_login_user_id) values('00000000000000000000000000000003', '中国区','00000000000000000000000000000003', '00000000000000000000000000000001');
--insert ignore into contest_division(id,name,contest_stage_id,create_login_user_id) values('00000000000000000000000000000004', '中国区','00000000000000000000000000000004', '00000000000000000000000000000001');


--insert ignore into contestant (id,contestant_type,name,age,agent_age,contest_item_id,create_login_user_id,organization_id,deleted,agent_login_user_id) values ('00000000000000000000000000000001', 'INDIVIDUAL','周杰伦',10,20,'00000000000000000000000000000001', '00000000000000000000000000000001', '00000000000000000000000000000001',false,'00000000000000000000000000000001');
--insert ignore into contestant (id,contestant_type,name,age,agent_age,contest_item_id,create_login_user_id,deleted,agent_login_user_id) values ('00000000000000000000000000000002', 'INDIVIDUAL','程磊',11,21,'00000000000000000000000000000002', '00000000000000000000000000000002',false,'00000000000000000000000000000002');
--
--insert ignore into division_contestant(id,contest_division_id,contestant_id,deleted) values ('00000000000000000000000000000001', '00000000000000000000000000000001', '00000000000000000000000000000001',false);
--insert ignore into division_contestant(id,contest_division_id,contestant_id,deleted) values ('00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false);
--
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score,video_url,url_mp4fd,url_mp4ld,url_mp4sd,urlm3u8fd,urlm3u8ld,urlm3u8sd) values('00000000000000000000000000000001', 'VIDEO','青花瓷','青 花 瓷','/home/frog/app/fitness-backend/works-dir/2018-12-26/40288a8b67e931430167e93e2aa40004.mp4','00000000000000000000000000000001', '00000000000000000000000000000001', '00000000000000000000000000000001',false,'SUCCEEDED','https://picsum.photos/512/512?id=4',0,0,1545041406462
--,',https://sample-videos.com/video123/mp4/720/big_buck_bunny_720p_5mb.mp4'
--,',https://sample-videos.com/video123/mp4/720/big_buck_bunny_720p_5mb.mp4'
--,',https://sample-videos.com/video123/mp4/720/big_buck_bunny_720p_5mb.mp4'
--,',https://sample-videos.com/video123/mp4/720/big_buck_bunny_720p_5mb.mp4'
--,',https://sample-videos.com/video123/mp4/720/big_buck_bunny_720p_5mb.mp4'
--,',https://sample-videos.com/video123/mp4/720/big_buck_bunny_720p_5mb.mp4'
--,',https://sample-videos.com/video123/mp4/720/big_buck_bunny_720p_5mb.mp4'
--);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000002', 'IMG','青花瓷','青 花 瓷','','00000000000000000000000000000001', '00000000000000000000000000000001', '00000000000000000000000000000001',false,'SUCCEEDED','https://picsum.photos/512/512?id=1',0,0,1545041406462);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000003', 'IMG','青花瓷3','青 花 瓷 3','','00000000000000000000000000000001', '00000000000000000000000000000001', '00000000000000000000000000000001',false,'SUCCEEDED','https://picsum.photos/512/512?id=2',0,0,1545041406463);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000004', 'IMG','青花瓷4','青 花 瓷 4','','00000000000000000000000000000001', '00000000000000000000000000000001', '00000000000000000000000000000001',false,'SUCCEEDED','https://picsum.photos/512/512?id=3',0,0,1545041406464);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000005', 'VIDEO','娘子','娘 子','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://sample-videos.com/video123/mp4/720/big_buck_bunny_720p_5mb.mp4',0,0,1545041406465);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000006', 'IMG','依然范特西','依 然 范 特 西 ','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=4',1000,199,1545041406466);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000007', 'IMG','依然范特西3','依 然 范 特 西 3','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=5',1000,199,1545041406467);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000008', 'IMG','依然范特西4','依 然 范 特 西 4','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=6',1000,199,1545041406468);
--
--
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000100', 'IMG','依然范特西100','依 然 范 特 西 1 0 0','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=100',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000101', 'IMG','依然范特西101','依 然 范 特 西 1 0 1','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=101',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000102', 'IMG','依然范特西102','依 然 范 x','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=102',1000,199,1545041406468); insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000103', 'IMG','依然范特西103','依 然 范 特 西 1 0 3','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=103',1000,199,1545041406468); insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000104', 'IMG','依然范特西104','依 然 范 特 西 1 0 4','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=104',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000105', 'IMG','依然范特西105','依 然 范 特 西 1 0 5','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=105',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000106', 'IMG','依然范特西106','依 然 范 特 西 1 0 6','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=106',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000107', 'IMG','依然范特西107','依 然 范 特 西 1 0 7','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=107',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000108', 'IMG','依然范特西108','依 然 范 特 西 1 0 8','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=108',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000109', 'IMG','依然范特西109','依 然 范 特 西 1 0 9','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=109',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000110', 'IMG','依然范特西110','依 然 范 特 西 1 1 0','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=110',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000111', 'IMG','依然范特西111','依 然 范 特 西 1 1 1','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=111',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000112', 'IMG','依然范特西112','依 然 范 特 西 1 1 2','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=112',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000113', 'IMG','依然范特西113','依 然 范 特 西 1 1 3','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=113',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000114', 'IMG','依然范特西114','依 然 范 特 西 1 1 4','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=114',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000115', 'IMG','依然范特西115','依 然 范 特 西 1 1 5','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=115',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000116', 'IMG','依然范特西116','依 然 范 特 西 1 1 6','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=116',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000117', 'IMG','依然范特西117','依 然 范 特 西 1 1 7','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=117',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000118', 'IMG','依然范特西118','依 然 范 特 西 1 1 8','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=118',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000119', 'IMG','依然范特西119','依 然 范 特 西 1 1 9','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=119',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000120', 'IMG','依然范特西120','依 然 范 特 西 1 2 0','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=120',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000121', 'IMG','依然范特西121','依 然 范 特 西 1 2 1','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=121',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000122', 'IMG','依然范特西122','依 然 范 特 西 1 2 2','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=122',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000123', 'IMG','依然范特西123','依 然 范 特 西 1 2 3','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=123',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000124', 'IMG','依然范特西124','依 然 范 特 西 1 2 4','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=124',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000125', 'IMG','依然范特西125','依 然 范 特 西 1 2 5','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=125',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000126', 'IMG','依然范特西126','依 然 范 特 西 1 2 6','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=126',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000127', 'IMG','依然范特西127','依 然 范 特 西 1 2 7','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=127',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000128', 'IMG','依然范特西128','依 然 范 特 西 1 2 8','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=128',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000129', 'IMG','依然范特西129','依 然 范 特 西 1 2 9','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=129',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000130', 'IMG','依然范特西130','依 然 范 特 西 1 3 0','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=130',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000131', 'IMG','依然范特西131','依 然 范 特 西 1 3 1','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=131',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000132', 'IMG','依然范特西132','依 然 范 特 西 1 3 2','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=132',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000133', 'IMG','依然范特西133','依 然 范 特 西 1 3 3','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=133',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000134', 'IMG','依然范特西134','依 然 范 特 西 1 3 4','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=134',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000135', 'IMG','依然范特西135','依 然 范 特 西 1 3 5','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=135',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000136', 'IMG','依然范特西136','依 然 范 特 西 1 3 6','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=136',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000137', 'IMG','依然范特西137','依 然 范 特 西 1 3 7','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=137',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000138', 'IMG','依然范特西138','依 然 范 特 西 1 3 8','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=138',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000139', 'IMG','依然范特西139','依 然 范 特 西 1 3 9','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=139',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000140', 'IMG','依然范特西140','依 然 范 特 西 1 4 0','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=140',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000141', 'IMG','依然范特西141','依 然 范 特 西 1 4 1','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=141',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000142', 'IMG','依然范特西142','依 然 范 特 西 1 4 2','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=142',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000143', 'IMG','依然范特西143','依 然 范 特 西 1 4 3','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=143',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000144', 'IMG','依然范特西144','依 然 范 特 西 1 4 4','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=144',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000145', 'IMG','依然范特西145','依 然 范 特 西 1 4 5','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=145',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000146', 'IMG','依然范特西146','依 然 范 特 西 1 4 6','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=146',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000147', 'IMG','依然范特西147','依 然 范 特 西 1 4 7','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=147',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000148', 'IMG','依然范特西148','依 然 范 特 西 1 4 8','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=148',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000149', 'IMG','依然范特西149','依 然 范 特 西 1 4 9','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=149',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000150', 'IMG','依然范特西150','依 然 范 特 西 1 5 0','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=150',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000151', 'IMG','依然范特西151','依 然 范 特 西 1 5 1','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=151',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000152', 'IMG','依然范特西152','依 然 范 特 西 1 5 2','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=152',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000153', 'IMG','依然范特西153','依 然 范 特 西 1 5 3','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=153',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000154', 'IMG','依然范特西154','依 然 范 特 西 1 5 4','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=154',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000155', 'IMG','依然范特西155','依 然 范 特 西 1 5 5','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=155',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000156', 'IMG','依然范特西156','依 然 范 特 西 1 5 6','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=156',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000157', 'IMG','依然范特西157','依 然 范 特 西 1 5 7','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=157',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000158', 'IMG','依然范特西158','依 然 范 特 西 1 5 8','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=158',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000159', 'IMG','依然范特西159','依 然 范 特 西 1 5 9','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=159',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000160', 'IMG','依然范特西160','依 然 范 特 西 1 6 0','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=160',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000161', 'IMG','依然范特西161','依 然 范 特 西 1 6 1','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=161',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000162', 'IMG','依然范特西162','依 然 范 特 西 1 6 2','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=162',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000163', 'IMG','依然范特西163','依 然 范 特 西 1 6 3','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=163',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000164', 'IMG','依然范特西164','依 然 范 特 西 1 6 4','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=164',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000165', 'IMG','依然范特西165','依 然 范 特 西 1 6 5','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=165',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000166', 'IMG','依然范特西166','依 然 范 特 西 1 6 6','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=166',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000167', 'IMG','依然范特西167','依 然 范 特 西 1 6 7','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=167',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000168', 'IMG','依然范特西168','依 然 范 特 西 1 6 8','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=168',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000169', 'IMG','依然范特西169','依 然 范 特 西 1 6 9','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=169',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000170', 'IMG','依然范特西170','依 然 范 特 西 1 7 0','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=170',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000171', 'IMG','依然范特西171','依 然 范 特 西 1 7 1','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=171',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000172', 'IMG','依然范特西172','依 然 范 特 西 1 7 2','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=172',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000173', 'IMG','依然范特西173','依 然 范 特 西 1 7 3','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=173',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000174', 'IMG','依然范特西174','依 然 范 特 西 1 7 4','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=174',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000175', 'IMG','依然范特西175','依 然 范 特 西 1 7 5','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=175',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000176', 'IMG','依然范特西176','依 然 范 特 西 1 7 6','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=176',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000177', 'IMG','依然范特西177','依 然 范 特 西 1 7 7','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=177',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000178', 'IMG','依然范特西178','依 然 范 特 西 1 7 8','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=178',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000179', 'IMG','依然范特西179','依 然 范 特 西 1 7 9','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=179',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000180', 'IMG','依然范特西180','依 然 范 特 西 1 8 0','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=180',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000181', 'IMG','依然范特西181','依 然 范 特 西 1 8 1','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=181',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000182', 'IMG','依然范特西182','依 然 范 特 西 1 8 2','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=182',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000183', 'IMG','依然范特西183','依 然 范 特 西 1 8 3','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=183',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000184', 'IMG','依然范特西184','依 然 范 特 西 1 8 4','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=184',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000185', 'IMG','依然范特西185','依 然 范 特 西 1 8 5','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=185',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000186', 'IMG','依然范特西186','依 然 范 特 西 1 8 6','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=186',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000187', 'IMG','依然范特西187','依 然 范 特 西 1 8 7','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=187',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000188', 'IMG','依然范特西188','依 然 范 特 西 1 8 8','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=188',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000189', 'IMG','依然范特西189','依 然 范 特 西 1 8 9','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=189',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000190', 'IMG','依然范特西190','依 然 范 特 西 1 9 0','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=190',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000191', 'IMG','依然范特西191','依 然 范 特 西 1 9 1','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=191',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000192', 'IMG','依然范特西192','依 然 范 特 西 1 9 2','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=192',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000193', 'IMG','依然范特西193','依 然 范 特 西 1 9 3','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=193',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000194', 'IMG','依然范特西194','依 然 范 特 西 1 9 4','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=194',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000195', 'IMG','依然范特西195','依 然 范 特 西 1 9 5','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=195',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000196', 'IMG','依然范特西196','依 然 范 特 西 1 9 6','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=196',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000197', 'IMG','依然范特西197','依 然 范 特 西 1 9 7','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=197',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000198', 'IMG','依然范特西198','依 然 范 特 西 1 9 8','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=198',1000,199,1545041406468);
--insert ignore into works(id,format,name,name_with_space,path,contestant_id,division_contestant_id,login_user_id,deleted,status,cover_url,user_likes,votes,score) values('00000000000000000000000000000199', 'IMG','依然范特西199','依 然 范 特 西 1 9 9','','00000000000000000000000000000002', '00000000000000000000000000000002', '00000000000000000000000000000002',false,'SUCCEEDED','https://picsum.photos/512/512?id=199',1000,199,1545041406468);
--

insert ignore into article(id,title,author,title_with_space,body) values ('00000000000000000000000000000001', '测试文章1','新东方在线','测 试 文 章 1','<p>测试</p>');
insert ignore into article(id,title,author,title_with_space,body) values ('00000000000000000000000000000002', '测试文章2','新东方在线','测 试 文 章 2','<p>测试</p>');
insert ignore into article(id,title,author,title_with_space,body) values ('00000000000000000000000000000003', '测试文章3','树艺🐸培训','测 试 文 章 3','<p>测试</p>');

insert ignore into feed(id,name) values ('homeFeed','首页');
insert ignore into feed(id,name) values ('eventFeed','赛事');



--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000001','-1000.0','homeFeed','BANNER',null,null,null,'false',null,'TOP',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000002','-999.0',null,'ARTICLE','00000000000000000000000000000001','https://picsum.photos/512/512?id=7','banner里的一篇文章','false','00000000000000000000000000000001',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000003','-998.0',null,'ORGANIZATION','00000000000000000000000000000001','https://picsum.photos/512/512?id=8','banner里的一个机构','false','00000000000000000000000000000001',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000004','-997.0',null,'FEED','00000000000000000000000000000001','https://picsum.photos/512/512?id=9','banner里的一个FEED','false','00000000000000000000000000000001',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000005','-996.0',null,'FEED','eventFeed','https://picsum.photos/512/512?id=10','banner里的另一个FEED','false','00000000000000000000000000000001',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000006','-995.0',null,'MY',null,'https://picsum.photos/512/512?id=11','banner里的一个个人中心','false','00000000000000000000000000000001',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000007','-994.0',null,'WORKS','00000000000000000000000000000001','https://picsum.photos/512/512?id=12','banner里的一个作品','false','00000000000000000000000000000001',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000008','-993.0',null,'WORKS','00000000000000000000000000000002','https://picsum.photos/512/512?id=13','banner里的另一个作品','false','00000000000000000000000000000001',null,false);
--
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000012','-989.0','homeFeed','BUTTON_GROUP',null,null,null,'false',null,'TOP',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000013','-988.0','homeFeed','ARTICLE','00000000000000000000000000000001','https://picsum.photos/512/512?id=14','Feed里的一篇文章','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000014','-987.0','homeFeed','ARTICLE','00000000000000000000000000000002','https://picsum.photos/512/512?id=15','Feed里的另一篇文章','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000015','-986.0','homeFeed','WORKS','00000000000000000000000000000001','https://picsum.photos/512/512?id=16','Feed里的一个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000016','-985.0','homeFeed','WORKS','00000000000000000000000000000002','https://picsum.photos/512/512?id=17','Feed里的另一个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000017','-984.0','homeFeed','WORKS','00000000000000000000000000000003','https://picsum.photos/512/512?id=18','Feed里的第三个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000018','-983.0','homeFeed','WORKS','00000000000000000000000000000004','https://picsum.photos/512/512?id=19','Feed里的第四个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000019','-982.0','homeFeed','WORKS','00000000000000000000000000000005','https://picsum.photos/512/512?id=20','Feed里的第五个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000020','-981.0','homeFeed','WORKS','00000000000000000000000000000006','https://picsum.photos/512/512?id=21','Feed里的第6个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000021','-980.0','homeFeed','WORKS','00000000000000000000000000000007','https://picsum.photos/512/512?id=22','Feed里的第7个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000022','-979.0','homeFeed','WORKS','00000000000000000000000000000008','https://picsum.photos/512/512?id=23','Feed里的第8个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000023','-978.0','homeFeed','WORKS','00000000000000000000000000000009','https://picsum.photos/512/512?id=23','Feed里的第9个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000024','-977.0','homeFeed','WORKS','00000000000000000000000000000010','https://picsum.photos/512/512?id=23','Feed里的第10个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000000025','-976.0','homeFeed','WORKS','00000000000000000000000000000011','https://picsum.photos/512/512?id=23','Feed里的第11个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010001','-1000.0','eventFeed','BANNER',null,null,null,'false',null,'TOP',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010002','-999.0',null,'ARTICLE','00000000000000000000000000000001','https://picsum.photos/512/512?id=24','banner里的一篇文章','false','00000000000000000000000000010001',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010003','-998.0',null,'ORGANIZATION','00000000000000000000000000000001','https://picsum.photos/512/512?id=25','banner里的一个机构','false','00000000000000000000000000010001',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010004','-997.0',null,'FEED','00000000000000000000000000000001','https://picsum.photos/512/512?id=26','banner里的一个FEED','false','00000000000000000000000000010001',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010005','-996.0',null,'FEED','eventFeed','https://picsum.photos/512/512?id=27','banner里的另一个FEED','false','00000000000000000000000000010001',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010006','-995.0',null,'MY',null,'https://picsum.photos/512/512?id=28','banner里的一个个人中心','false','00000000000000000000000000010001',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010007','-994.0',null,'WORKS','00000000000000000000000000000001','https://picsum.photos/512/512?id=29','banner里的一个作品','false','00000000000000000000000000010001',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010008','-993.0',null,'WORKS','00000000000000000000000000000002','https://picsum.photos/512/512?id=30','banner里的另一个作品','false','00000000000000000000000000010001',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010009','-992.0','eventFeed','ORGANIZATIONS',null,null,null,'false',null,'TOP',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010010','-991.0',null,'ORGANIZATION','00000000000000000000000000000001','https://picsum.photos/512/512?id=31','机构列表里的第一个机构','false','00000000000000000000000000010009',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010011','-990.0',null,'ORGANIZATION','00000000000000000000000000000002','https://picsum.photos/512/512?id=32','机构列表里的第二个机构','false','00000000000000000000000000010009',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010012','-990.0',null,'ORGANIZATION','00000000000000000000000000000003','https://picsum.photos/512/512?id=32','机构列表里的第三个机构','false','00000000000000000000000000010009',null,false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010013','-988.0','eventFeed','ARTICLE','00000000000000000000000000000001','https://picsum.photos/512/512?id=33','Feed里的一篇文章','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010014','-987.0','eventFeed','ARTICLE','00000000000000000000000000000002','https://picsum.photos/512/512?id=34','Feed里的另一篇文章','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010015','-986.0','eventFeed','WORKS','00000000000000000000000000000001','https://picsum.photos/512/512?id=35','Feed里的一个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010016','-985.0','eventFeed','WORKS','00000000000000000000000000000002','https://picsum.photos/512/512?id=36','Feed里的另一个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010017','-984.0','eventFeed','WORKS','00000000000000000000000000000003','https://picsum.photos/512/512?id=37','Feed里的第三个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010018','-983.0','eventFeed','WORKS','00000000000000000000000000000004','https://picsum.photos/512/512?id=38','Feed里的第四个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010019','-982.0','eventFeed','WORKS','00000000000000000000000000000005','https://picsum.photos/512/512?id=39','Feed里的第五个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010020','-981.0','eventFeed','WORKS','00000000000000000000000000000006','https://picsum.photos/512/512?id=40','Feed里的第6个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010021','-980.0','eventFeed','WORKS','00000000000000000000000000000007','https://picsum.photos/512/512?id=41','Feed里的第7个作品','false',null,'BOTTOM',false);
--
--insert ignore into feed_item(id,score,feed_id,entity_type,entity,img,title,is_video,parent_id,feed_position,deleted)
-- values('00000000000000000000000000010022','-979.0','eventFeed','WORKS','00000000000000000000000000000008','https://picsum.photos/512/512?id=42','Feed里的第8个作品','false',null,'BOTTOM',false);
--
--
--
----insert ignore into login_user_rank(id, login_user_id, statistical_date, user_rank, followers,votes)
----    values('00000000000000000000000000000001','00000000000000000000000000000001',current_date(),1,12345,1234567);
----insert ignore into login_user_rank(id, login_user_id, statistical_date, user_rank, followers,votes)
----    values('00000000000000000000000000000002','00000000000000000000000000000001',date_add(current_date(),interval -1 day),1+5,12345-67,1234567-890);
----insert ignore into login_user_rank(id, login_user_id, statistical_date, user_rank, followers,votes)
----    values('00000000000000000000000000000003','00000000000000000000000000000002',current_date(),1,12345,1234567);
----insert ignore into login_user_rank(id, login_user_id, statistical_date, user_rank, followers,votes)
----    values('00000000000000000000000000000004','00000000000000000000000000000002',date_add(current_date(),interval -1 day),1+5,12345-67,1234567-890);
--
--insert ignore into vote(id,works_id,login_user_id,create_time) values ('00000000000000000000000000000001','00000000000000000000000000000001','00000000000000000000000000000001',date_add(current_date(),interval -1 day));
--insert ignore into vote(id,works_id,login_user_id,create_time) values ('00000000000000000000000000000002','00000000000000000000000000000001','00000000000000000000000000000002',date_add(current_date(),interval -1 day));
--insert ignore into vote(id,works_id,login_user_id,create_time) values ('00000000000000000000000000000003','00000000000000000000000000000002','00000000000000000000000000000003',date_add(current_date(),interval -1 day));
--insert ignore into vote(id,works_id,login_user_id,create_time) values ('00000000000000000000000000000004','00000000000000000000000000000005','00000000000000000000000000000004',date_add(current_date(),interval -1 day));
--insert ignore into vote(id,works_id,login_user_id,create_time) values ('00000000000000000000000000000005','00000000000000000000000000000005','00000000000000000000000000000001',date_add(current_date(),interval -1 day));
--insert ignore into vote(id,works_id,login_user_id,create_time) values ('00000000000000000000000000000006','00000000000000000000000000000005','00000000000000000000000000000002',date_add(current_date(),interval -1 day));
--insert ignore into vote(id,works_id,login_user_id,create_time) values ('00000000000000000000000000000007','00000000000000000000000000000006','00000000000000000000000000000003',date_add(current_date(),interval -1 day));
--insert ignore into vote(id,works_id,login_user_id,create_time) values ('00000000000000000000000000000008','00000000000000000000000000000006','00000000000000000000000000000005',date_add(current_date(),interval -1 day));
--
--insert ignore into user_like(id,works_id,login_user_id) values ('00000000000000000000000000000001','00000000000000000000000000000001','00000000000000000000000000000001');
--insert ignore into user_like(id,works_id,login_user_id) values ('00000000000000000000000000000002','00000000000000000000000000000001','00000000000000000000000000000002');
--insert ignore into user_like(id,works_id,login_user_id) values ('00000000000000000000000000000003','00000000000000000000000000000001','00000000000000000000000000000003');
--insert ignore into user_like(id,works_id,login_user_id) values ('00000000000000000000000000000004','00000000000000000000000000000001','00000000000000000000000000000004');
--insert ignore into user_like(id,works_id,login_user_id) values ('00000000000000000000000000000005','00000000000000000000000000000005','00000000000000000000000000000001');
--insert ignore into user_like(id,works_id,login_user_id) values ('00000000000000000000000000000006','00000000000000000000000000000005','00000000000000000000000000000002');
--insert ignore into user_like(id,works_id,login_user_id) values ('00000000000000000000000000000007','00000000000000000000000000000005','00000000000000000000000000000003');
--insert ignore into user_like(id,works_id,login_user_id) values ('00000000000000000000000000000008','00000000000000000000000000000005','00000000000000000000000000000004');
--
--
--insert ignore into contest_schedule(id,contest_id,schedule_time,logo,content,location_name,entity_type,entity)
--    values ('00000000000000000000000000000001','00000000000000000000000000000001','2019-01-10','https://picsum.photos/512/512?id=01','8进4 半决赛','中国科技馆','IN_LINK','https://www.baidu.com');
--insert ignore into contest_schedule(id,contest_id,schedule_time,logo,content,location_name,entity_type,entity)
--    values ('00000000000000000000000000000002','00000000000000000000000000000001','2019-01-09','https://picsum.photos/512/512?id=02','8进4 半决赛','中国科技馆','IN_LINK','https://www.baidu.com');
--insert ignore into contest_schedule(id,contest_id,schedule_time,logo,content,location_name, entity_type,entity)
--    values ('00000000000000000000000000000003','00000000000000000000000000000001','2019-01-08','https://picsum.photos/512/512?id=03','导演今天感冒了\n写了一首诗\n这样就显得文案很长\n也不是很整齐\n大概就像这样了\n长到超出屏幕也没关系','中国科技馆','IN_LINK','https://www.baidu.com');
--insert ignore into contest_schedule(id,contest_id,schedule_time,logo,content,location_name, entity_type,entity)
--    values ('00000000000000000000000000000004','00000000000000000000000000000001','2019-01-07','https://picsum.photos/512/512?id=04','盒饭不够\n字数来凑','中国科技馆','IN_LINK','https://www.baidu.com');
--
--
--insert ignore into system_message(id,source_login_user_id,tag,content,create_time) values ('00000000000000000000000000000001','00000000000000000000000000000006','官方','我是前天一条测试消息',date_add(current_date(),interval -2 day));
--insert ignore into system_message(id,source_login_user_id,tag,content,create_time) values ('00000000000000000000000000000002','00000000000000000000000000000006','官方','我是昨天一条测试消息',date_add(current_date(),interval -1 day));
--insert ignore into system_message(id,source_login_user_id,tag,content,create_time) values ('00000000000000000000000000000003','00000000000000000000000000000006',null,'我是今天一条测试消息',current_date());
--
--
--
--
--insert ignore into organization (id, name, logo, summary) values ('00000000000000000000000000000003', '树艺🐸培训','https://picsum.photos/512/512?orgizationId=00000000000000000000000000000003','简介\n简介....\n简介....');
--insert ignore into contestant (id,contestant_type,name,age,agent_age,contest_item_id,organization_id,best_works_id,deleted) values ('00000000000000000000000000030001', 'INDIVIDUAL','大娃',11,21,'00000000000000000000000000000002','00000000000000000000000000000003','00000000000000000000000000030001',false);
--insert ignore into works(id,format,name,contestant_id,status,cover_url,user_likes,votes,score,deleted) values('00000000000000000000000000030001', 'IMG','青花瓷','00000000000000000000000000030001', 'SUCCEEDED', 'https://picsum.photos/512/512?worksId=00000000000000000000000000030001',230,23,1546395621160,false);
--insert ignore into contestant (id,contestant_type,name,age,agent_age,contest_item_id,organization_id,best_works_id,deleted) values ('00000000000000000000000000030002', 'INDIVIDUAL','二娃',11,21,'00000000000000000000000000000002','00000000000000000000000000000003','00000000000000000000000000030002',false);
--insert ignore into works(id,format,name,contestant_id,status,cover_url,user_likes,votes,score,deleted) values('00000000000000000000000000030002', 'IMG','依然范特西','00000000000000000000000000030002', 'SUCCEEDED', 'https://picsum.photos/512/512?worksId=00000000000000000000000000030002',230,25,1546395621160,false);
--insert ignore into contestant (id,contestant_type,name,age,agent_age,contest_item_id,organization_id,best_works_id,deleted) values ('00000000000000000000000000030003', 'INDIVIDUAL','三娃',11,21,'00000000000000000000000000000002','00000000000000000000000000000003','00000000000000000000000000030003',false);
--insert ignore into works(id,format,name,contestant_id,status,cover_url,user_likes,votes,score,deleted) values('00000000000000000000000000030003', 'IMG','简单爱','00000000000000000000000000030003', 'SUCCEEDED', 'https://picsum.photos/512/512?worksId=00000000000000000000000000030003',240,23,1546395621160,false);
--insert ignore into contestant (id,contestant_type,name,age,agent_age,contest_item_id,organization_id,best_works_id,deleted) values ('00000000000000000000000000030004', 'INDIVIDUAL','四娃',11,21,'00000000000000000000000000000002','00000000000000000000000000000003','00000000000000000000000000030004',false);
--insert ignore into works(id,format,name,contestant_id,status,cover_url,user_likes,votes,score,deleted) values('00000000000000000000000000030004', 'IMG','范特西','00000000000000000000000000030004', 'SUCCEEDED', 'https://picsum.photos/512/512?worksId=00000000000000000000000000030004',220,23,1546395621160,false);
--insert ignore into contestant (id,contestant_type,name,age,agent_age,contest_item_id,organization_id,best_works_id,deleted) values ('00000000000000000000000000030005', 'INDIVIDUAL','五娃',11,21,'00000000000000000000000000000002','00000000000000000000000000000003','00000000000000000000000000030005',false);
--insert ignore into works(id,format,name,contestant_id,status,cover_url,user_likes,votes,score,deleted) values('00000000000000000000000000030005', 'IMG','开不了口','00000000000000000000000000030005', 'SUCCEEDED', 'https://picsum.photos/512/512?worksId=00000000000000000000000000030005',220,23,1546395621160,false);
--insert ignore into contestant (id,contestant_type,name,age,agent_age,contest_item_id,organization_id,best_works_id,deleted) values ('00000000000000000000000000030006', 'INDIVIDUAL','六娃',11,21,'00000000000000000000000000000002','00000000000000000000000000000003','00000000000000000000000000030006',false);
--insert ignore into works(id,format,name,contestant_id,status,cover_url,user_likes,votes,score,deleted) values('00000000000000000000000000030006', 'IMG','头文字D','00000000000000000000000000030006', 'SUCCEEDED', 'https://picsum.photos/512/512?worksId=00000000000000000000000000030006',220,23,1546395621160,false);
--insert ignore into contestant (id,contestant_type,name,age,agent_age,contest_item_id,organization_id,best_works_id,deleted) values ('00000000000000000000000000030007', 'INDIVIDUAL','七娃',11,21,'00000000000000000000000000000002','00000000000000000000000000000003','00000000000000000000000000030007',false);
--insert ignore into works(id,format,name,contestant_id,status,cover_url,user_likes,votes,score,deleted) values('00000000000000000000000000030007', 'IMG','八度空间','00000000000000000000000000030007', 'SUCCEEDED', 'https://picsum.photos/512/512?worksId=00000000000000000000000000030007',220,23,1546395621160,false);
--insert ignore into contestant (id,contestant_type,name,age,agent_age,contest_item_id,organization_id,best_works_id,deleted) values ('00000000000000000000000000030008', 'INDIVIDUAL','jay',11,21,'00000000000000000000000000000002','00000000000000000000000000000003','00000000000000000000000000030008',false);
--insert ignore into works(id,format,name,contestant_id,status,cover_url,user_likes,votes,score,deleted) values('00000000000000000000000000030008', 'IMG','jay','00000000000000000000000000030008', 'SUCCEEDED', 'https://picsum.photos/512/512?worksId=00000000000000000000000000030008',220,23,1546395621160,false);
--insert ignore into contestant (id,contestant_type,name,age,agent_age,contest_item_id,organization_id,best_works_id,deleted) values ('00000000000000000000000000030009', 'INDIVIDUAL','我很忙',11,21,'00000000000000000000000000000002','00000000000000000000000000000003','00000000000000000000000000030009',false);
--insert ignore into works(id,format,name,contestant_id,status,cover_url,user_likes,votes,score,deleted) values('00000000000000000000000000030009', 'IMG','我很忙','00000000000000000000000000030009', 'SUCCEEDED', 'https://picsum.photos/512/512?worksId=00000000000000000000000000030009',220,23,1546395621161,false);
--insert ignore into contestant (id,contestant_type,name,age,agent_age,contest_item_id,organization_id,best_works_id,deleted) values ('00000000000000000000000000030010', 'INDIVIDUAL','肖邦',11,21,'00000000000000000000000000000002','00000000000000000000000000000003','00000000000000000000000000030010',false);
--insert ignore into works(id,format,name,contestant_id,status,cover_url,user_likes,votes,score,deleted) values('00000000000000000000000000030010', 'IMG','11月的肖邦','00000000000000000000000000030010', 'SUCCEEDED', 'https://picsum.photos/512/512?worksId=00000000000000000000000000030010',220,23,1546395621161,false);
--insert ignore into contestant (id,contestant_type,name,age,agent_age,contest_item_id,organization_id,best_works_id,deleted) values ('00000000000000000000000000030010', 'INDIVIDUAL','秘密',11,21,'00000000000000000000000000000002','00000000000000000000000000000003','00000000000000000000000000030010',false);
--insert ignore into works(id,format,name,contestant_id,status,cover_url,user_likes,votes,score,deleted) values('00000000000000000000000000030010', 'IMG','不能说的秘密','00000000000000000000000000030010', 'SUCCEEDED', 'https://picsum.photos/512/512?worksId=00000000000000000000000000030010',220,23,1546395621161,false);


insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score)
values('00000000000000000000000000000001','小红花','小红花','https://picsum.photos/512/512?taskId=00000000000000000000000000000001&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000000001&status=inactivated',null,null,now(),'GROUP',null,null,6);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic)
values('00000000000000000000000000001001','注册成功','注册成功','https://picsum.photos/512/512?taskId=00000000000000000000000000001001&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000001001&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000001',2,"#context.isRegistered()");
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic)
values('00000000000000000000000000001002','绑定微信','绑定微信','https://picsum.photos/512/512?taskId=00000000000000000000000000001002&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000001002&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000001',1,"#context.isBindWeiXin()");


insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score)
values('00000000000000000000000000000002','每天见面','每天见面','https://picsum.photos/512/512?taskId=00000000000000000000000000000002&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000000002&status=inactivated',null,null,now(),'GROUP',null,null,5);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000002001',null,'"连续登陆"+#data.intValue()+"天"','https://picsum.photos/512/512?taskId=00000000000000000000000000002001&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000002001&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000002',6,'#context.continuousLoginDays() >= #data.intValue()',2);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000002002',null,'"连续登陆"+#data.intValue()+"天"','https://picsum.photos/512/512?taskId=00000000000000000000000000002002&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000002002&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000002',5,'#context.continuousLoginDays() >= #data.intValue()',3);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000002003',null,'"连续登陆"+#data.intValue()+"天"','https://picsum.photos/512/512?taskId=00000000000000000000000000002003&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000002003&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000003',4,'#context.continuousLoginDays() >= #data.intValue()',4);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000002004',null,'"连续登陆"+#data.intValue()+"天"','https://picsum.photos/512/512?taskId=00000000000000000000000000002004&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000002004&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000004',3,'#context.continuousLoginDays() >= #data.intValue()',5);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000002005',null,'"连续登陆"+#data.intValue()+"天"','https://picsum.photos/512/512?taskId=00000000000000000000000000002005&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000002005&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000005',2,'#context.continuousLoginDays() >= #data.intValue()',7);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000002006',null,'"连续登陆"+#data.intValue()+"天"','https://picsum.photos/512/512?taskId=00000000000000000000000000002006&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000002006&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000006',1,'#context.continuousLoginDays() >= #data.intValue()',10);


insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score)
values('00000000000000000000000000000003','赞','赞','https://picsum.photos/512/512?taskId=00000000000000000000000000000002&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000000002&status=inactivated',null,null,now(),'GROUP',null,null,4);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000003001',null,'"点赞"+#data.intValue()+"次"','https://picsum.photos/512/512?taskId=00000000000000000000000000003001&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003001&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000003',4,'#context.likeActionCount() >= #data.intValue()',1);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000003002',null,'"点赞"+#data.intValue()+"次"','https://picsum.photos/512/512?taskId=00000000000000000000000000003002&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003002&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000003',3,'#context.likeActionCount() >= #data.intValue()',5);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000003003',null,'"点赞"+#data.intValue()+"次"','https://picsum.photos/512/512?taskId=00000000000000000000000000003003&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003003&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000003',2,'#context.likeActionCount() >= #data.intValue()',10);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000003004',null,'"点赞"+#data.intValue()+"次"','https://picsum.photos/512/512?taskId=00000000000000000000000000003004&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003004&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000003',1,'#context.likeActionCount() >= #data.intValue()',20);


insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score)
values('00000000000000000000000000000004','投票吧!','投票吧!','https://picsum.photos/512/512?taskId=00000000000000000000000000000004&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000000004&status=inactivated',null,null,now(),'GROUP',null,null,3);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000004001',null,'"投票"+#data.intValue()+"次"','https://picsum.photos/512/512?taskId=00000000000000000000000000004001&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003001&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000004',4,'#context.voteActionCount() >= #data.intValue()',1);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000004002',null,'"投票"+#data.intValue()+"次"','https://picsum.photos/512/512?taskId=00000000000000000000000000004002&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003002&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000004',3,'#context.voteActionCount() >= #data.intValue()',5);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000004003',null,'"投票"+#data.intValue()+"次"','https://picsum.photos/512/512?taskId=00000000000000000000000000004003&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003003&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000004',2,'#context.voteActionCount() >= #data.intValue()',10);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000004004',null,'"投票"+#data.intValue()+"次"','https://picsum.photos/512/512?taskId=00000000000000000000000000004004&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003004&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000004',1,'#context.voteActionCount() >= #data.intValue()',20);


insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score)
values('00000000000000000000000000000005','博爱蛙!','博爱蛙!','https://picsum.photos/512/512?taskId=00000000000000000000000000000005&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000000005&status=inactivated',null,null,now(),'GROUP',null,null,2);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000005001',null,'"投给"+#data.intValue()+"人"','https://picsum.photos/512/512?taskId=00000000000000000000000000005001&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003001&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000005',4,'#context.voteToUserCount() >= #data.intValue()',5);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000005002',null,'"投给"+#data.intValue()+"人"','https://picsum.photos/512/512?taskId=00000000000000000000000000005002&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003002&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000005',3,'#context.voteToUserCount() >= #data.intValue()',10);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000005003',null,'"投给"+#data.intValue()+"人"','https://picsum.photos/512/512?taskId=00000000000000000000000000005003&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003003&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000005',2,'#context.voteToUserCount() >= #data.intValue()',15);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000005004',null,'"投给"+#data.intValue()+"人"','https://picsum.photos/512/512?taskId=00000000000000000000000000005004&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003004&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000005',1,'#context.voteToUserCount() >= #data.intValue()',20);


insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score)
values('00000000000000000000000000000006','人气蛙','人气蛙','https://picsum.photos/512/512?taskId=00000000000000000000000000000006&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000000006&status=inactivated',null,null,now(),'GROUP',null,null,1);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000006001',null,'"被投"+#data.intValue()+"票"','https://picsum.photos/512/512?taskId=00000000000000000000000000006001&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003001&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000006',4,'#context.gotVoteCount() >= #data.intValue()',10);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000006002',null,'"被投"+#data.intValue()+"票"','https://picsum.photos/512/512?taskId=00000000000000000000000000006002&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003002&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000006',3,'#context.gotVoteCount() >= #data.intValue()',50);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000006003',null,'"被投"+#data.intValue()+"票"','https://picsum.photos/512/512?taskId=00000000000000000000000000006003&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003003&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000006',2,'#context.gotVoteCount() >= #data.intValue()',100);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000006004',null,'"被投"+#data.intValue()+"票"','https://picsum.photos/512/512?taskId=00000000000000000000000000006004&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003004&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000006',1,'#context.gotVoteCount() >= #data.intValue()',200);


insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score)
values('00000000000000000000000000000007','金蛙蛙','金蛙蛙','https://picsum.photos/512/512?taskId=00000000000000000000000000000007&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000000007&status=inactivated',null,null,now(),'GROUP','00000000000000000000000000000001',null,8);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000007001','蛙到成功','"报名活动并获得"+#data.intValue()+"票"','https://picsum.photos/512/512?taskId=00000000000000000000000000007001&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003001&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000007',5,'#context.isAppliedContest() && #context.gotVoteCountInContest() >= #data.intValue()',10);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000007002','星蛙棋布','"获得"+#data.get(\'voteCountInContest\') +"票和"+#data.get(\'userLikeCountInContest\')+"赞"','https://picsum.photos/512/512?taskId=00000000000000000000000000007002&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003002&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000007',4,'#context.gotVoteCountInContest() >= #data.get(\'voteCountInContest\') && #context.gotUserLikeCountInContest() >= #data.get(\'userLikeCountInContest\')','{voteCountInContest:100,userLikeCountInContest:100}');
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000007003','蛙来蛙往','"连续"+#data.intValue()+"天 每天都有投票和点赞行为"','https://picsum.photos/512/512?taskId=00000000000000000000000000007003&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003003&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000007',3,'#context.continuousVoteOrLikeInContest() >= #data.intValue()',60);
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data)
values('00000000000000000000000000007004','一蛙当千','"获得"+#data.get(\'voteCountInContest\')+"票和"+#data.get(\'userLikeCountInContest\') +"赞 连续登录"+#data.get(\'continuousLoginDays\')+"天"','https://picsum.photos/512/512?taskId=00000000000000000000000000007004&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003004&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000007',2,'#context.gotVoteCountInContest() >= #data.get(\'voteCountInContest\') && #context.gotUserLikeCountInContest() >= #data.get(\'userLikeCountInContest\') && #context.continuousLoginDays() >= #data.get(\'continuousLoginDays\') ','{voteCountInContest:10000,userLikeCountInContest:50000,continuousLoginDays:100}');
insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic,data,reward_wa)
values('00000000000000000000000000007005','蛙吞山河','"获得全部4个分支勋章 活动名次前"+#data.intValue()+""','https://picsum.photos/512/512?taskId=00000000000000000000000000007005&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000003005&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000007',1,'#context.isLastTaskInGroup() && #context.rank() <= #data.intValue()' ,'100',1);

insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score)
values('00000000000000000000000000000009','666蛙','666蛙','https://picsum.photos/512/512?taskId=00000000000000000000000000000001&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000000001&status=inactivated',null,null,now(),'GROUP',null,null,6);


insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic)
values('00000000000000000000000000009001','赛蛙','赛蛙','https://picsum.photos/512/512?taskId=00000000000000000000000000001002&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000001002&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000009',1,"#context.isBindWeiXin()");

insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic)
values('00000000000000000000000000009002','不停的蛙','不停的蛙','https://picsum.photos/512/512?taskId=00000000000000000000000000001002&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000001002&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000009',2,"#context.isBindWeiXin()");

insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic)
values('00000000000000000000000000009003','超努力的蛙','超努力的蛙','https://picsum.photos/512/512?taskId=00000000000000000000000000001002&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000001002&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000009',3,"#context.isBindWeiXin()");

insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic)
values('00000000000000000000000000009004','非常蛙','非常蛙','https://picsum.photos/512/512?taskId=00000000000000000000000000001002&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000001002&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000009',4,"#context.isBindWeiXin()");

insert ignore into login_user_task(id,name,logic_desc,activated_image,inactivated_image,start_time,end_time,create_time,task_type,contest_season,parent_id,score,logic)
values('00000000000000000000000000009005','常胜蛙','常胜蛙','https://picsum.photos/512/512?taskId=00000000000000000000000000001002&status=activated'
,'https://picsum.photos/512/512?taskId=00000000000000000000000000001002&status=inactivated',null,null,now(),'INDIVIDUAL',null,'00000000000000000000000000000009',5,"#context.isBindWeiXin()");




replace into sms_template (id, content, create_time) values ('00000000000000000000000000000001','预置短信内容1',now());
replace into sms_template (id, content, create_time) values ('00000000000000000000000000000002','预置短信内容2',now());
replace into sms_template (id, content, create_time) values ('00000000000000000000000000000003','预置短信内容3',now());

--SET FOREIGN_KEY_CHECKS=1;

