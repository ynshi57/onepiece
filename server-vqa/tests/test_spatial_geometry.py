from app.spatial_geometry import diagnose


def surface(id,x=0,z=0,epoch=1, vertices=None):
    return {'id':id,'epoch':epoch,'kind':'horizontal','vertices':vertices or [{'x':x+a,'y':z,'z':b} for a,b in [(0,0),(1,0),(1,1),(0,1)]]}


def test_overlap_candidate_preserves_original():
    doc={'surfaces':[surface('a'),surface('b',x=.5)]}
    report=diagnose(doc)
    assert len(report['candidates'])==1
    assert not report['fusionPerformed'] and report['originalPreserved']
    assert doc['surfaces'][0]['vertices'][0]['x']==0


def test_separate_touching_height_and_epoch():
    for other,reason in [(surface('b',x=2),'no_polygon_overlap'),(surface('b',x=1),'no_polygon_overlap'),(surface('b',z=.2),'different_plane'),(surface('b',epoch=2),'different_epoch'),(surface('b',epoch=None),'unknown_epoch')]:
        report=diagnose({'surfaces':[surface('a'),other]})
        assert report['candidates']==[]
        assert report['rejectedPairReasons']=={reason:1}


def test_identical_polygon_positive_overlap():
    assert len(diagnose({'surfaces':[surface('a'),surface('b')]})['candidates'])==1


def test_degenerate_plane_rejected():
    s=surface('bad',vertices=[{'x':a,'y':0,'z':0} for a in range(3)])
    assert diagnose({'surfaces':[s]})['planes'][0]['status']=='invalid'


def test_bbox_overlap_inside_concave_void_not_candidate():
    a=surface('a',vertices=[{'x':x,'y':0,'z':z} for x,z in [(0,0),(3,0),(3,1),(1,1),(1,3),(0,3)]])
    b=surface('b',vertices=[{'x':x,'y':0,'z':z} for x,z in [(2,2),(2.5,2),(2.5,2.5),(2,2.5)]])
    report=diagnose({'surfaces':[a,b]})
    assert report['candidates']==[]
    assert report['rejectedPairReasons']=={'no_polygon_overlap':1}


def test_nonplanar_and_wall_corner_rejected():
    bad=surface('bad')
    bad['vertices'][0]['y']=.5
    assert diagnose({'surfaces':[bad]})['planes'][0]['status']=='non_planar'
    wall=surface('wall',vertices=[{'x':0,'y':y,'z':z} for y,z in [(0,0),(1,0),(1,1),(0,1)]])
    assert diagnose({'surfaces':[surface('a'),wall]})['rejectedPairReasons']=={'different_normal':1}
